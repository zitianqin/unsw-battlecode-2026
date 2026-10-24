/* judge_engine.h served by the engine compiled ahead of time (wasm2c's translation of unswbc_engine.wasm), in place of
   engine_wasmtime.c. Not built by default: measured 2026-09-28 on 32 real games, it took whole-game CPU from 0.807 to
   0.788 of today's path (the wasmtime backend's), on macOS with guard pages, too little for wabt in the loop home, a
   12 MB translation per engine and minute-long builds on the hosts. Kept for when the engine's share grows. To build:
     wabt 1.0.42 (wabt-1.0.42-macos-arm64.tar.gz sha256 3f654779b436c628db3ca4323c51538815f5fbc887a6d5f3bb1c18d36281e240,
     wabt-1.0.42.tar.xz sha256 a76cda3c174a43097863a07fc0b0c202f770f53e21806ea2636f167d1ffb1e30 for wasm2c/wasm-rt-*);
     wasm2c unswbc_engine.wasm -n engine -o generated/engine.c --num-outputs 8
     cc -O3 -flto [-DWASM_RT_MEMCHECK_BOUNDS_CHECK=1 off macOS] -DENGINE_SHA256='"<sha256>"' -I generated -I <wabt>/wasm2c
        play.c engine_w2c.c generated/engine_[0-7].c <wabt>/wasm2c/wasm-rt{,-mem,-exceptions}-impl.c -o native-judge
   Byte-identical on those 32 games and 184 recorded ones. It serves the imports as wasmtime's default WasiConfig
   does: unswbc.bot_reply (a reply too long for the engine's buffer kept and given whole when the engine asks again,
   engine.py's state["long"]), unswbc.bot_spawn, unswbc.log, and the nine WASI calls wasi-libc makes (no environment,
   no preopened folders, stderr to the host, other output dropped, proc_exit a trap). A callback that must end the
   match (an OS error) asks for a trap after it returns, which unwinds only wasm2c's own frames. */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "engine.h" /* wasm2c's declarations of the translated engine */
#include "judge_engine.h"
#include "wasm-rt-impl.h"

#ifndef ENGINE_SHA256
#error "build with -DENGINE_SHA256=\"<the engine sha256>\" (see the comment above)"
#endif

struct w2c_unswbc {
    w2c_engine *e;
    uint8_t *reply;
    uint32_t reply_len, reply_cap;
    int long_pending;
    int abort; /* a callback asked to end the match */
    const engine_callbacks *c; /* NULL while the replay is written */
    int c_errno;
};

struct w2c_wasi__snapshot__preview1 {
    struct w2c_unswbc *h;
};

static uint8_t *MEM(struct w2c_unswbc *h) { return h->e->w2c_memory.data; }

static void set_reply(struct w2c_unswbc *h, const uint8_t *data, uint32_t len) {
    if (len > h->reply_cap) {
        h->reply_cap = len * 2 + 64;
        h->reply = realloc(h->reply, h->reply_cap);
        if (!h->reply) wasm_rt_trap(WASM_RT_TRAP_UNREACHABLE);
    }
    if (len) memcpy(h->reply, data, len);
    h->reply_len = len;
}

static void check_range(struct w2c_unswbc *h, uint64_t at, uint64_t len) {
    if (at + len > h->e->w2c_memory.size) wasm_rt_trap(WASM_RT_TRAP_OOB);
}

u32 w2c_unswbc_bot_reply(struct w2c_unswbc *h, u32 dragon, u32 ptr, u32 len, u32 out, u32 cap) {
    if (!h->long_pending) {
        check_range(h, ptr, len);
        const uint8_t *reply;
        size_t reply_len;
        if (!h->c || h->c->reply(h->c->ctx, (int32_t)dragon, MEM(h) + ptr, len, &reply, &reply_len) != 0) {
            h->abort = 1;
            h->c_errno = errno;
            wasm_rt_trap(WASM_RT_TRAP_UNREACHABLE);
        }
        set_reply(h, reply, (uint32_t)reply_len);
    }
    if (h->reply_len > cap) { /* too long for the buffer: the engine grows it and asks again */
        h->long_pending = 1;
        return h->reply_len;
    }
    h->long_pending = 0;
    check_range(h, out, h->reply_len);
    if (h->reply_len) memcpy(MEM(h) + out, h->reply, h->reply_len);
    return h->reply_len;
}

void w2c_unswbc_bot_spawn(struct w2c_unswbc *h, u32 dragon, u32 ptr, u32 len) {
    check_range(h, ptr, len);
    if (h->c) h->c->spawn(h->c->ctx, (int32_t)dragon, MEM(h) + ptr, len);
}

void w2c_unswbc_log(struct w2c_unswbc *h, u32 dragon, u32 round, u32 reason) {
    if (h->c) h->c->death(h->c->ctx, (int32_t)dragon, (int32_t)round, (int)reason);
}

/* ---- WASI preview1 ---- */

#define WASI_EBADF 8
#define WASI_ESPIPE 70

static void put32(struct w2c_unswbc *h, u32 at, uint32_t value) {
    check_range(h, at, 4);
    memcpy(MEM(h) + at, &value, 4);
}

u32 w2c_wasi__snapshot__preview1_environ_sizes_get(struct w2c_wasi__snapshot__preview1 *w, u32 count, u32 size) {
    put32(w->h, count, 0);
    put32(w->h, size, 0);
    return 0;
}

u32 w2c_wasi__snapshot__preview1_environ_get(struct w2c_wasi__snapshot__preview1 *w, u32 environ, u32 buffer) {
    (void)w, (void)environ, (void)buffer;
    return 0;
}

u32 w2c_wasi__snapshot__preview1_clock_time_get(struct w2c_wasi__snapshot__preview1 *w, u32 id, u64 precision,
                                               u32 out) {
    (void)precision;
    struct timespec t;
    clock_gettime(id == 0 ? CLOCK_REALTIME : CLOCK_MONOTONIC, &t);
    u64 ns = (u64)t.tv_sec * 1000000000ull + (u64)t.tv_nsec;
    check_range(w->h, out, 8);
    memcpy(MEM(w->h) + out, &ns, 8);
    return 0;
}

u32 w2c_wasi__snapshot__preview1_fd_close(struct w2c_wasi__snapshot__preview1 *w, u32 fd) {
    (void)w, (void)fd;
    return 0;
}

u32 w2c_wasi__snapshot__preview1_fd_prestat_get(struct w2c_wasi__snapshot__preview1 *w, u32 fd, u32 out) {
    (void)w, (void)fd, (void)out;
    return WASI_EBADF;
}

u32 w2c_wasi__snapshot__preview1_fd_prestat_dir_name(struct w2c_wasi__snapshot__preview1 *w, u32 fd, u32 path,
                                                     u32 len) {
    (void)w, (void)fd, (void)path, (void)len;
    return WASI_EBADF;
}

u32 w2c_wasi__snapshot__preview1_fd_seek(struct w2c_wasi__snapshot__preview1 *w, u32 fd, u64 offset, u32 whence,
                                        u32 out) {
    (void)w, (void)fd, (void)offset, (void)whence, (void)out;
    return WASI_ESPIPE;
}

u32 w2c_wasi__snapshot__preview1_fd_write(struct w2c_wasi__snapshot__preview1 *w, u32 fd, u32 iovs, u32 count,
                                         u32 written) {
    struct w2c_unswbc *h = w->h;
    uint32_t total = 0;
    check_range(h, iovs, 8ull * count);
    for (uint32_t i = 0; i < count; i++) {
        uint32_t base, len;
        memcpy(&base, MEM(h) + iovs + 8 * i, 4);
        memcpy(&len, MEM(h) + iovs + 8 * i + 4, 4);
        check_range(h, base, len);
        if (fd == 2 && len && h->c && h->c->notice) h->c->notice(h->c->ctx, MEM(h) + base, len);
        total += len;
    }
    put32(h, written, total);
    return 0;
}

void w2c_wasi__snapshot__preview1_proc_exit(struct w2c_wasi__snapshot__preview1 *w, u32 code) {
    (void)w, (void)code;
    wasm_rt_trap(WASM_RT_TRAP_UNREACHABLE);
}

/* ---- one instance ---- */

typedef struct host {
    w2c_engine e;
    struct w2c_unswbc h;
    struct w2c_wasi__snapshot__preview1 w;
    int live;
    char error[256];
} host;

static int initialised;

static void reset(host *x) {
    if (x->live) wasm2c_engine_free(&x->e);
    x->live = 0;
    free(x->h.reply);
    memset(&x->h, 0, sizeof x->h);
    x->h.e = &x->e;
    x->w.h = &x->h;
}

static void note_trap(host *x, wasm_rt_trap_t trap) {
    snprintf(x->error, sizeof x->error, "wasm trap: %s", wasm_rt_strerror(trap));
}

/* A fresh instance (a new wasmtime Store) after _initialize: 0, or the trap. */
static int instantiate(host *x) {
    if (!initialised) {
        wasm_rt_init();
        initialised = 1;
    }
    wasm_rt_trap_t trap = wasm_rt_impl_try();
    if (trap) {
        note_trap(x, trap);
        return (int)trap;
    }
    wasm2c_engine_instantiate(&x->e, &x->h, &x->w);
    x->live = 1;
    w2c_engine_0x5Finitialize(&x->e);
    return 0;
}

/* ---- calls into the engine: each returns its value, or -1000 - trap on a trap ---- */

static uint8_t *host_memory(host *x) { return x->e.w2c_memory.data; }

#define TRY(expr)                                     \
    do {                                              \
        wasm_rt_trap_t trap = wasm_rt_impl_try();     \
        if (trap) {                                   \
            note_trap(x, trap);                       \
            return -1000 - (int64_t)trap;             \
        }                                             \
        return (int64_t)(expr);                       \
    } while (0)

static int64_t host_alloc(host *x, uint32_t n) { TRY(w2c_engine_ubc_alloc(&x->e, n)); }
static int64_t host_free_ptr(host *x, uint32_t p) { TRY((w2c_engine_ubc_free(&x->e, p), 0)); }
static int64_t host_error_ptr(host *x) { TRY(w2c_engine_ubc_error(&x->e)); }
static int64_t host_ubc_run(host *x, uint32_t map, uint32_t len, uint32_t debug, uint64_t seed, uint32_t out) {
    TRY((int32_t)w2c_engine_ubc_run(&x->e, map, len, debug, seed, out));
}
static int64_t host_ubc_replay(host *x, uint32_t a, uint32_t al, uint32_t b, uint32_t bl) {
    TRY((int32_t)w2c_engine_ubc_replay(&x->e, a, al, b, bl));
}
static int64_t host_ubc_replay_ptr(host *x) { TRY(w2c_engine_ubc_replay_ptr(&x->e)); }

/* ---- judge_engine.h: for play.c ---- */

struct engine {
    host x;
    uint8_t *replay;
};

static void set_err(char *err, size_t n, const char *text) { snprintf(err, n, "%s", text); }

engine *engine_open(const engine_options *o, char *err, size_t n) {
    if (strcmp(o->sha256, ENGINE_SHA256) != 0) {
        snprintf(err, n, "this engine build is engine %.16s, not %.16s", ENGINE_SHA256, o->sha256);
        return NULL;
    }
    engine *e = calloc(1, sizeof *e);
    if (!e) set_err(err, n, "out of memory");
    return e;
}

static int64_t put(host *x, const void *bytes, size_t len) {
    int64_t at = host_alloc(x, (uint32_t)len);
    if (at < 0) return at;
    if (at + len > x->e.w2c_memory.size) return -1000 - WASM_RT_TRAP_OOB;
    if (len) memcpy(host_memory(x) + at, bytes, len);
    return at;
}

static void read_error(host *x, char *err, size_t n) {
    int64_t at = host_error_ptr(x);
    if (at < 0 || (uint64_t)at >= x->e.w2c_memory.size) {
        set_err(err, n, x->error);
        return;
    }
    const uint8_t *base = host_memory(x);
    size_t end = (size_t)at;
    while (end < x->e.w2c_memory.size && base[end]) end++;
    if (end == (size_t)at)
        set_err(err, n, "engine failed");
    else
        snprintf(err, n, "%.*s", (int)(end - (size_t)at), (const char *)base + at);
}

int engine_run(engine *e, const engine_callbacks *callbacks, const uint8_t *map, size_t map_len, int32_t debug,
               uint64_t seed, int32_t result[12], char *err, size_t n) {
    host *x = &e->x;
    reset(x);
    x->h.c = callbacks;
    if (instantiate(x) != 0) return set_err(err, n, x->error), ENGINE_BROKEN;
    int64_t map_ptr = put(x, map, map_len), out_ptr = map_ptr < 0 ? map_ptr : host_alloc(x, 12 * 4);
    if (out_ptr < 0) return set_err(err, n, x->error), ENGINE_BROKEN;
    int64_t code = host_ubc_run(x, (uint32_t)map_ptr, (uint32_t)map_len, (uint32_t)debug, seed, (uint32_t)out_ptr);
    if (x->h.abort) {
        errno = x->h.c_errno;
        return ENGINE_ABORTED;
    }
    if (code <= -1000) return set_err(err, n, x->error), ENGINE_BROKEN;
    if (code != 0) {
        read_error(x, err, n);
        return ENGINE_FAILED;
    }
    if ((uint64_t)out_ptr + 12 * 4 > x->e.w2c_memory.size) return set_err(err, n, "results out of bounds"), ENGINE_BROKEN;
    memcpy(result, host_memory(x) + out_ptr, 12 * 4); /* little-endian, as wasm */
    return ENGINE_OK;
}

int engine_replay(engine *e, const char *a, const char *b, const uint8_t **data, size_t *len, char *err, size_t n) {
    host *x = &e->x;
    x->h.c = NULL;
    int64_t pa = put(x, a, strlen(a)), pb = pa < 0 ? pa : put(x, b, strlen(b));
    if (pb < 0) return set_err(err, n, x->error), ENGINE_BROKEN;
    int64_t size = host_ubc_replay(x, (uint32_t)pa, (uint32_t)strlen(a), (uint32_t)pb, (uint32_t)strlen(b));
    if (host_free_ptr(x, (uint32_t)pa) < 0 || host_free_ptr(x, (uint32_t)pb) < 0 || size <= -1000)
        return set_err(err, n, x->error), ENGINE_BROKEN;
    if (size < 0) {
        read_error(x, err, n);
        return ENGINE_FAILED;
    }
    int64_t base = host_ubc_replay_ptr(x);
    if (base < 0 || (uint64_t)base + (uint64_t)size > x->e.w2c_memory.size)
        return set_err(err, n, "replay out of bounds"), ENGINE_BROKEN;
    free(e->replay);
    if (!(e->replay = malloc(size ? (size_t)size : 1))) return set_err(err, n, "out of memory"), ENGINE_BROKEN;
    memcpy(e->replay, host_memory(x) + base, (size_t)size);
    *data = e->replay;
    *len = (size_t)size;
    return ENGINE_OK;
}

void engine_close(engine *e) {
    if (!e) return;
    reset(&e->x);
    free(e->replay);
    free(e);
}
