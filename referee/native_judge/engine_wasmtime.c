/* judge_engine.h served by wasmtime's C API, from the library wasmtime-py ships (dlopen), so the engine runs exactly as the
   toolkit's engine.py runs it: a default Engine, a fresh Store with a WASI config that inherits nothing and sends
   stderr to a callback, the three unswbc imports, _initialize, then ubc_run and ubc_replay.

   The compiled engine is engine_cache.py's file (same name, same "bc-engine-cache sha256 HEX\n" header and checks:
   only this user can have written it or its folder, and its content matches the checksum), since both sides use the
   same wasmtime. A missing or unusable copy is compiled from the wasm, whose sha256 must then be the one this build
   was made for, and saved the way engine_cache.save_copy saves it. */
#if defined(__linux__)
#define _GNU_SOURCE
#endif
#include "judge_engine.h"
#include "wasmtime_min.h"

#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define DECLARE(ret, name, params) static ret(*p_##name) params;
WASMTIME_FUNCTIONS(DECLARE)
#undef DECLARE

static void set_err(char *err, size_t len, const char *format, ...) {
    va_list args;
    va_start(args, format);
    vsnprintf(err, len, format, args);
    va_end(args);
}

/* ---- sha256 (FIPS 180-4), for the cache's checksum and the wasm's digest ---- */

typedef struct sha256 {
    uint32_t h[8];
    uint64_t length;
    uint8_t block[64];
    size_t used;
} sha256;

static const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98,
    0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8,
    0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819,
    0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
    0xc67178f2};

#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void sha256_compress(sha256 *s, const uint8_t *p) {
    uint32_t w[64], a, b, c, d, e, f, g, h;
    for (int i = 0; i < 16; i++)
        w[i] = (uint32_t)p[4 * i] << 24 | (uint32_t)p[4 * i + 1] << 16 | (uint32_t)p[4 * i + 2] << 8 | p[4 * i + 3];
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    a = s->h[0], b = s->h[1], c = s->h[2], d = s->h[3], e = s->h[4], f = s->h[5], g = s->h[6], h = s->h[7];
    for (int i = 0; i < 64; i++) {
        uint32_t t1 = h + (ROR(e, 6) ^ ROR(e, 11) ^ ROR(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i];
        uint32_t t2 = (ROR(a, 2) ^ ROR(a, 13) ^ ROR(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
        h = g, g = f, f = e, e = d + t1, d = c, c = b, b = a, a = t1 + t2;
    }
    s->h[0] += a, s->h[1] += b, s->h[2] += c, s->h[3] += d, s->h[4] += e, s->h[5] += f, s->h[6] += g, s->h[7] += h;
}

/* The sha256 of data as 64 lowercase hex digits and a NUL. */
static void sha256_hex(const uint8_t *data, size_t len, char out[65]) {
    sha256 s = {{0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19}, 0,
                {0}, 0};
    s.length = (uint64_t)len * 8;
    size_t whole = len - len % 64;
    for (size_t at = 0; at < whole; at += 64) sha256_compress(&s, data + at);
    uint8_t tail[128] = {0};
    size_t rest = len - whole;
    memcpy(tail, data + whole, rest);
    tail[rest] = 0x80;
    size_t blocks = rest + 9 <= 64 ? 1 : 2;
    for (int i = 0; i < 8; i++) tail[blocks * 64 - 1 - i] = (uint8_t)(s.length >> (8 * i));
    for (size_t i = 0; i < blocks; i++) sha256_compress(&s, tail + 64 * i);
    for (int i = 0; i < 8; i++) snprintf(out + 8 * i, 9, "%08x", s.h[i]);
}

/* ---- the cache file ---- */

static const char HEADER[] = "bc-engine-cache sha256 ";

/* True when only this user could have written path (engine_cache.private). */
static int private_path(const char *path) {
    struct stat st;
    return stat(path, &st) == 0 && st.st_uid == getuid() && !(st.st_mode & 022);
}

static int read_file(const char *path, uint8_t **data, size_t *len) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    struct stat st;
    if (fstat(fd, &st) != 0) {
        close(fd);
        return -1;
    }
    uint8_t *buffer = malloc(st.st_size ? (size_t)st.st_size : 1);
    size_t have = 0;
    while (buffer && have < (size_t)st.st_size) {
        ssize_t got = read(fd, buffer + have, (size_t)st.st_size - have);
        if (got < 0 && errno == EINTR) continue;
        if (got <= 0) break;
        have += (size_t)got;
    }
    close(fd);
    if (!buffer || have != (size_t)st.st_size) {
        free(buffer);
        errno = EIO;
        return -1;
    }
    *data = buffer;
    *len = have;
    return 0;
}

static void say(const char *format, ...) {
    char text[1024];
    va_list args;
    va_start(args, format);
    vsnprintf(text, sizeof text, format, args);
    va_end(args);
    for (char *c = text; *c; c++)
        if (*c == '\n' || *c == '\r') *c = ' ';
    fprintf(stderr, "engine cache: %s\n", text);
}

static void error_text(wasmtime_error_t *error, char *out, size_t len) {
    wasm_byte_vec_t message;
    p_wasmtime_error_message(error, &message);
    snprintf(out, len, "%.*s", (int)message.size, message.data);
    p_wasm_byte_vec_delete(&message);
    p_wasmtime_error_delete(error);
}

static void trap_text(wasm_trap_t *trap, char *out, size_t len) {
    wasm_byte_vec_t message;
    p_wasm_trap_message(trap, &message);
    int size = (int)message.size;
    if (size > 0 && message.data[size - 1] == '\0') size--;
    snprintf(out, len, "%.*s", size, message.data);
    p_wasm_byte_vec_delete(&message);
    p_wasm_trap_delete(trap);
}

/* The compiled bytes of a checked copy (engine_cache.read_copy), or -1 with why in err. */
static int read_copy(const char *cwasm, uint8_t **file, const uint8_t **blob, size_t *blob_len, char *err, size_t n) {
    char folder[4096];
    snprintf(folder, sizeof folder, "%s", cwasm);
    char *slash = strrchr(folder, '/');
    if (slash) *slash = '\0';
    if (!private_path(cwasm) || !private_path(slash ? folder : ".")) {
        set_err(err, n, "%s could have been written by someone else", cwasm);
        return -1;
    }
    size_t len;
    if (read_file(cwasm, file, &len) != 0) {
        set_err(err, n, "%s", strerror(errno));
        return -1;
    }
    const uint8_t *newline = memchr(*file, '\n', len);
    size_t header = sizeof HEADER - 1;
    char digest[65];
    if (newline && (size_t)(newline - *file) == header + 64 && memcmp(*file, HEADER, header) == 0) {
        *blob = newline + 1;
        *blob_len = len - (size_t)(*blob - *file);
        sha256_hex(*blob, *blob_len, digest);
        if (memcmp(digest, *file + header, 64) == 0) return 0;
    }
    set_err(err, n, "damaged: its content doesn't match its checksum");
    free(*file);
    *file = NULL;
    return -1;
}

/* Saves compiled bytes as cwasm: owner-only, flushed to disk, then renamed into place whole (engine_cache.save_copy). */
static int save_copy(const char *cwasm, const uint8_t *blob, size_t len, char *err, size_t n) {
    char folder[4096], temporary[4200];
    snprintf(folder, sizeof folder, "%s", cwasm);
    char *slash = strrchr(folder, '/');
    if (slash) {
        *slash = '\0';
        mkdir(folder, 0700);
    }
    snprintf(temporary, sizeof temporary, "%s.%d.tmp", cwasm, (int)getpid());
    int fd = open(temporary, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0) {
        set_err(err, n, "%s", strerror(errno));
        return -1;
    }
    char digest[65], line[128];
    sha256_hex(blob, len, digest);
    int header = snprintf(line, sizeof line, "%s%s\n", HEADER, digest);
    int ok = write(fd, line, (size_t)header) == header;
    for (size_t at = 0; ok && at < len;) {
        ssize_t put = write(fd, blob + at, len - at);
        if (put < 0 && errno == EINTR) continue;
        ok = put > 0;
        if (ok) at += (size_t)put;
    }
    ok = ok && fsync(fd) == 0;
    int saved = errno;
    close(fd);
    if (!ok || rename(temporary, cwasm) != 0) {
        if (ok) saved = errno;
        unlink(temporary);
        set_err(err, n, "%s", strerror(saved));
        return -1;
    }
    return 0;
}

/* ---- the engine ---- */

struct engine {
    void *library;
    wasm_engine_t *engine;
    wasmtime_module_t *module;
    wasmtime_store_t *store;
    wasmtime_context_t *context;
    wasmtime_linker_t *linker;
    wasmtime_instance_t instance;
    wasmtime_memory_t memory;
    wasmtime_func_t alloc, free_, run, replay, replay_ptr, error;
    const engine_callbacks *callbacks;
    uint8_t *long_reply; /* a reply the engine's buffer could not hold, kept for its retry (engine.py's "long") */
    size_t long_len;
    int aborted, aborted_errno;
    uint8_t *replay_copy;
};

static int load_library(engine *e, const char *path, char *err, size_t n) {
    e->library = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!e->library) {
        set_err(err, n, "cannot load %s: %s", path, dlerror());
        return -1;
    }
#define RESOLVE(ret, name, params)                                          \
    if (!(p_##name = (ret(*) params)dlsym(e->library, #name))) {            \
        set_err(err, n, "%s has no %s", path, #name);                        \
        return -1;                                                          \
    }
    WASMTIME_FUNCTIONS(RESOLVE)
#undef RESOLVE
    return 0;
}

/* The module: the cached copy, else compiled from the wasm (checked against the digest this build serves) and saved. */
static int load_module(engine *e, const engine_options *o, char *err, size_t n) {
    char why[1024];
    const char *name = o->cwasm ? (strrchr(o->cwasm, '/') ? strrchr(o->cwasm, '/') + 1 : o->cwasm) : NULL;
    if (name && (strncmp(name, o->sha256, 64) != 0 || name[64] != '-')) {
        set_err(err, n, "%s is not a copy of engine %.16s", name, o->sha256);
        return -1;
    }
    if (o->cwasm && access(o->cwasm, F_OK) == 0) {
        uint8_t *file = NULL;
        const uint8_t *blob;
        size_t len;
        if (read_copy(o->cwasm, &file, &blob, &len, why, sizeof why) == 0) {
            wasmtime_error_t *error = p_wasmtime_module_deserialize(e->engine, blob, len, &e->module);
            free(file);
            if (!error) return 0;
            error_text(error, why, sizeof why);
        }
        say("cannot load %s (%s); compiling the engine and replacing it", name, why);
    }
    uint8_t *wasm;
    size_t wasm_len;
    if (read_file(o->wasm, &wasm, &wasm_len) != 0) {
        set_err(err, n, "cannot read %s: %s", o->wasm, strerror(errno));
        return -1;
    }
    char digest[65];
    sha256_hex(wasm, wasm_len, digest);
    if (strcmp(digest, o->sha256) != 0) {
        free(wasm);
        set_err(err, n, "%s is engine %.16s, not %.16s this judge was built for", o->wasm, digest, o->sha256);
        return -1;
    }
    wasmtime_error_t *error = p_wasmtime_module_new(e->engine, wasm, wasm_len, &e->module);
    free(wasm);
    if (error) {
        error_text(error, why, sizeof why);
        set_err(err, n, "cannot compile the engine: %s", why);
        return -1;
    }
    if (o->cwasm) {
        wasm_byte_vec_t blob;
        error = p_wasmtime_module_serialize(e->module, &blob);
        if (error) {
            error_text(error, why, sizeof why);
            say("cannot save %s: %s", o->cwasm, why);
        } else {
            if (save_copy(o->cwasm, (const uint8_t *)blob.data, blob.size, why, sizeof why) != 0)
                say("cannot save %s: %s", o->cwasm, why);
            p_wasm_byte_vec_delete(&blob);
        }
    }
    return 0;
}

engine *engine_open(const engine_options *o, char *err, size_t n) {
    engine *e = calloc(1, sizeof *e);
    if (!e) {
        set_err(err, n, "out of memory");
        return NULL;
    }
    if (load_library(e, o->lib, err, n) != 0 || !(e->engine = p_wasm_engine_new())) {
        if (e->library && !e->engine && !*err) set_err(err, n, "wasm_engine_new failed");
        engine_close(e);
        return NULL;
    }
    if (load_module(e, o, err, n) != 0) {
        engine_close(e);
        return NULL;
    }
    return e;
}

static uint8_t *memory(engine *e, wasmtime_context_t *context, uint32_t at, size_t len) {
    size_t size = p_wasmtime_memory_data_size(context, &e->memory);
    if ((size_t)at > size || len > size - at) return NULL;
    return p_wasmtime_memory_data(context, &e->memory) + at;
}

static wasm_trap_t *trap(const char *message) { return p_wasmtime_trap_new(message, strlen(message)); }

static wasm_trap_t *on_reply(void *env, wasmtime_caller_t *caller, wasmtime_val_raw_t *raw, size_t num) {
    (void)num;
    engine *e = env;
    wasmtime_context_t *context = p_wasmtime_caller_context(caller);
    int32_t id = raw[0].i32;
    uint32_t ptr = (uint32_t)raw[1].i32, length = (uint32_t)raw[2].i32, out = (uint32_t)raw[3].i32;
    size_t cap = (uint32_t)raw[4].i32;
    const uint8_t *reply;
    size_t reply_len;
    uint8_t *kept = e->long_reply;
    if (kept) {
        reply = kept, reply_len = e->long_len;
        e->long_reply = NULL;
    } else {
        const uint8_t *block = memory(e, context, ptr, length);
        if (!block) return trap("bot_reply: block out of bounds");
        if (e->callbacks->reply(e->callbacks->ctx, id, block, length, &reply, &reply_len) != 0) {
            e->aborted = 1, e->aborted_errno = errno;
            return trap("bot_reply: aborted");
        }
    }
    if (reply_len > cap) { /* too long for the buffer: the engine grows it and asks again */
        if (!kept) {
            kept = malloc(reply_len ? reply_len : 1);
            if (!kept) return trap("out of memory");
            memcpy(kept, reply, reply_len);
        }
        e->long_reply = kept, e->long_len = reply_len;
        raw[0].i32 = (int32_t)reply_len;
        return NULL;
    }
    uint8_t *target = memory(e, context, out, reply_len);
    if (!target) return trap("bot_reply: buffer out of bounds");
    memcpy(target, reply, reply_len);
    free(kept);
    raw[0].i32 = (int32_t)reply_len;
    return NULL;
}

static wasm_trap_t *on_log(void *env, wasmtime_caller_t *caller, wasmtime_val_raw_t *raw, size_t num) {
    (void)caller, (void)num;
    engine *e = env;
    e->callbacks->death(e->callbacks->ctx, raw[0].i32, raw[1].i32, raw[2].i32);
    return NULL;
}

static wasm_trap_t *on_spawn(void *env, wasmtime_caller_t *caller, wasmtime_val_raw_t *raw, size_t num) {
    (void)num;
    engine *e = env;
    const uint8_t *init = memory(e, p_wasmtime_caller_context(caller), (uint32_t)raw[1].i32, (uint32_t)raw[2].i32);
    if (!init) return trap("bot_spawn: init out of bounds");
    e->callbacks->spawn(e->callbacks->ctx, raw[0].i32, init, (uint32_t)raw[2].i32);
    return NULL;
}

static ptrdiff_t on_stderr(void *env, const unsigned char *data, size_t len) {
    engine *e = env;
    if (e->callbacks && e->callbacks->notice) e->callbacks->notice(e->callbacks->ctx, data, len);
    return (ptrdiff_t)len;
}

static wasm_functype_t *functype(size_t params, size_t results) {
    wasm_valtype_t *in[5], *out[1];
    for (size_t i = 0; i < params; i++) in[i] = p_wasm_valtype_new(WASM_I32);
    for (size_t i = 0; i < results; i++) out[i] = p_wasm_valtype_new(WASM_I32);
    wasm_valtype_vec_t in_vec, out_vec;
    p_wasm_valtype_vec_new(&in_vec, params, in);
    if (results)
        p_wasm_valtype_vec_new(&out_vec, results, out);
    else
        p_wasm_valtype_vec_new_empty(&out_vec);
    return p_wasm_functype_new(&in_vec, &out_vec);
}

/* Calls an export: args in raw[0..], the first result back in raw[0]. -1 with err set on a trap or error. */
static int call(engine *e, wasmtime_func_t *func, wasmtime_val_raw_t *raw, size_t num, char *err, size_t n) {
    wasm_trap_t *caught = NULL;
    wasmtime_error_t *error = p_wasmtime_func_call_unchecked(e->context, func, raw, num, &caught);
    if (error) {
        error_text(error, err, n);
        return -1;
    }
    if (caught) {
        trap_text(caught, err, n);
        return -1;
    }
    return 0;
}

static int export_of(engine *e, const char *name, int kind, wasmtime_extern_t *item, char *err, size_t n) {
    if (!p_wasmtime_instance_export_get(e->context, &e->instance, name, strlen(name), item) || item->kind != kind) {
        set_err(err, n, "the engine lacks export %s", name);
        return -1;
    }
    return 0;
}

static int instantiate(engine *e, char *err, size_t n) {
    wasmtime_error_t *error;
    if (!(e->store = p_wasmtime_store_new(e->engine, NULL, NULL))) return set_err(err, n, "no store"), -1;
    e->context = p_wasmtime_store_context(e->store);
    wasi_config_t *wasi = p_wasi_config_new();
    p_wasi_config_set_stderr_custom(wasi, on_stderr, e, NULL);
    if ((error = p_wasmtime_context_set_wasi(e->context, wasi))) return error_text(error, err, n), -1;
    if (!(e->linker = p_wasmtime_linker_new(e->engine))) return set_err(err, n, "no linker"), -1;
    if ((error = p_wasmtime_linker_define_wasi(e->linker))) return error_text(error, err, n), -1;
    struct {
        const char *name;
        size_t params, results;
        wasmtime_func_unchecked_callback_t callback;
    } imports[] = {{"bot_reply", 5, 1, on_reply}, {"log", 3, 0, on_log}, {"bot_spawn", 3, 0, on_spawn}};
    for (size_t i = 0; i < 3; i++) {
        wasm_functype_t *type = functype(imports[i].params, imports[i].results);
        error = p_wasmtime_linker_define_func_unchecked(e->linker, "unswbc", 6, imports[i].name,
                                                        strlen(imports[i].name), type, imports[i].callback, e, NULL);
        p_wasm_functype_delete(type);
        if (error) return error_text(error, err, n), -1;
    }
    wasm_trap_t *caught = NULL;
    if ((error = p_wasmtime_linker_instantiate(e->linker, e->context, e->module, &e->instance, &caught)))
        return error_text(error, err, n), -1;
    if (caught) return trap_text(caught, err, n), -1;
    wasmtime_extern_t item;
    struct {
        const char *name;
        wasmtime_func_t *into;
    } funcs[] = {{"ubc_alloc", &e->alloc}, {"ubc_free", &e->free_}, {"ubc_run", &e->run},
                 {"ubc_replay", &e->replay}, {"ubc_replay_ptr", &e->replay_ptr}, {"ubc_error", &e->error}};
    if (export_of(e, "memory", WASMTIME_EXTERN_MEMORY, &item, err, n) != 0) return -1;
    e->memory = item.of.memory;
    for (size_t i = 0; i < 6; i++) {
        if (export_of(e, funcs[i].name, WASMTIME_EXTERN_FUNC, &item, err, n) != 0) return -1;
        *funcs[i].into = item.of.func;
    }
    if (p_wasmtime_instance_export_get(e->context, &e->instance, "_initialize", 11, &item) &&
        item.kind == WASMTIME_EXTERN_FUNC) {
        wasmtime_val_raw_t raw[1];
        if (call(e, &item.of.func, raw, 0, err, n) != 0) return -1;
    }
    return 0;
}

/* ubc_alloc(len) with bytes copied in: the pointer, or -1. */
static int64_t put(engine *e, const void *bytes, size_t len, char *err, size_t n) {
    wasmtime_val_raw_t raw[1] = {{.i32 = (int32_t)len}};
    if (call(e, &e->alloc, raw, 1, err, n) != 0) return -1;
    uint8_t *at = memory(e, e->context, (uint32_t)raw[0].i32, len);
    if (!at) return set_err(err, n, "ubc_alloc gave memory out of bounds"), -1;
    memcpy(at, bytes, len);
    return (uint32_t)raw[0].i32;
}

/* ubc_error's message, as engine.py's _read_error reads it. */
static void engine_error(engine *e, char *err, size_t n) {
    wasmtime_val_raw_t raw[1];
    if (call(e, &e->error, raw, 0, err, n) != 0) return;
    uint32_t at = (uint32_t)raw[0].i32;
    size_t size = p_wasmtime_memory_data_size(e->context, &e->memory);
    const uint8_t *base = p_wasmtime_memory_data(e->context, &e->memory);
    size_t end = at;
    while (end < size && base[end]) end++;
    if (end == at) {
        set_err(err, n, "engine failed");
        return;
    }
    set_err(err, n, "%.*s", (int)(end - at), (const char *)base + at);
}

int engine_run(engine *e, const engine_callbacks *callbacks, const uint8_t *map, size_t map_len, int32_t debug,
               uint64_t seed, int32_t result[12], char *err, size_t n) {
    e->callbacks = callbacks;
    if (instantiate(e, err, n) != 0) return ENGINE_BROKEN;
    int64_t map_ptr = put(e, map, map_len, err, n);
    if (map_ptr < 0) return ENGINE_BROKEN;
    wasmtime_val_raw_t raw[5] = {{.i32 = 12 * 4}};
    if (call(e, &e->alloc, raw, 1, err, n) != 0) return ENGINE_BROKEN;
    uint32_t out_ptr = (uint32_t)raw[0].i32;
    raw[0].i32 = (int32_t)map_ptr, raw[1].i32 = (int32_t)map_len, raw[2].i32 = debug;
    raw[3].i64 = (int64_t)seed; /* wasm has no unsigned i64: the top half of the range goes in negative */
    raw[4].i32 = (int32_t)out_ptr;
    if (call(e, &e->run, raw, 5, err, n) != 0) {
        if (e->aborted) {
            errno = e->aborted_errno;
            return ENGINE_ABORTED;
        }
        return ENGINE_BROKEN;
    }
    if (raw[0].i32 != 0) {
        engine_error(e, err, n);
        return ENGINE_FAILED;
    }
    const uint8_t *values = memory(e, e->context, out_ptr, 12 * 4);
    if (!values) return set_err(err, n, "results out of bounds"), ENGINE_BROKEN;
    for (int i = 0; i < 12; i++)
        result[i] = (int32_t)((uint32_t)values[4 * i] | (uint32_t)values[4 * i + 1] << 8 |
                              (uint32_t)values[4 * i + 2] << 16 | (uint32_t)values[4 * i + 3] << 24);
    return ENGINE_OK;
}

int engine_replay(engine *e, const char *a, const char *b, const uint8_t **data, size_t *len, char *err, size_t n) {
    int64_t pa = put(e, a, strlen(a), err, n), pb = pa < 0 ? -1 : put(e, b, strlen(b), err, n);
    if (pb < 0) return ENGINE_BROKEN;
    wasmtime_val_raw_t raw[4] = {{.i32 = (int32_t)pa}, {.i32 = (int32_t)strlen(a)}, {.i32 = (int32_t)pb},
                                 {.i32 = (int32_t)strlen(b)}};
    if (call(e, &e->replay, raw, 4, err, n) != 0) return ENGINE_BROKEN;
    int32_t size = raw[0].i32;
    for (int i = 0; i < 2; i++) {
        wasmtime_val_raw_t freed[1] = {{.i32 = (int32_t)(i ? pb : pa)}};
        if (call(e, &e->free_, freed, 1, err, n) != 0) return ENGINE_BROKEN;
    }
    if (size < 0) {
        engine_error(e, err, n);
        return ENGINE_FAILED;
    }
    if (call(e, &e->replay_ptr, raw, 0, err, n) != 0) return ENGINE_BROKEN;
    const uint8_t *at = memory(e, e->context, (uint32_t)raw[0].i32, (size_t)size);
    if (!at) return set_err(err, n, "replay out of bounds"), ENGINE_BROKEN;
    free(e->replay_copy);
    if (!(e->replay_copy = malloc(size ? (size_t)size : 1))) return set_err(err, n, "out of memory"), ENGINE_BROKEN;
    memcpy(e->replay_copy, at, (size_t)size);
    *data = e->replay_copy;
    *len = (size_t)size;
    return ENGINE_OK;
}

void engine_close(engine *e) {
    if (!e) return;
    if (e->linker) p_wasmtime_linker_delete(e->linker);
    if (e->store) p_wasmtime_store_delete(e->store);
    if (e->module) p_wasmtime_module_delete(e->module);
    if (e->engine) p_wasm_engine_delete(e->engine);
    free(e->long_reply);
    free(e->replay_copy);
    free(e);
}
