/* The organisers' game engine as the judge drives it: open it once, run one match, take the replay. play.c uses only
   this interface, so another runtime for the same unswbc_engine.wasm (wasm2c or w2c2 output compiled ahead of time)
   can replace engine_wasmtime.c without touching the protocol code (engine_w2c.c is one, unbuilt by default), as
   long as it serves the same imports:
   unswbc.bot_spawn, unswbc.bot_reply and unswbc.log (engine.py's), and WASI with nothing inherited but stderr, which
   goes to `notice`. The engine owns every rule; nothing here decides anything about the game. */
#ifndef JUDGE_ENGINE_H
#define JUDGE_ENGINE_H

#include <stddef.h>
#include <stdint.h>

typedef struct engine_callbacks {
    void *ctx;
    /* The reply to dragon `id`'s round block, left in *out (valid until the next call). Returns 0, or -1 to abort
       the match with an OS error (errno), as a Python exception in engine.py's _bot_reply aborts it. */
    int (*reply)(void *ctx, int32_t id, const uint8_t *block, size_t len, const uint8_t **out, size_t *out_len);
    void (*spawn)(void *ctx, int32_t id, const uint8_t *init, size_t len);
    void (*death)(void *ctx, int32_t id, int32_t round, int reason);
    /* Bytes the engine wrote to its stderr (notices about map defects), in the pieces it wrote them. */
    void (*notice)(void *ctx, const uint8_t *text, size_t len);
} engine_callbacks;

typedef struct engine_options {
    const char *lib;     /* the runtime library, for engine_wasmtime.c wasmtime-py's _libwasmtime */
    const char *wasm;    /* unswbc_engine.wasm */
    const char *cwasm;   /* the engine compiled for this runtime (engine_cache.py's file), or NULL */
    const char *sha256;  /* the engine's sha256 (hex) this build was made for: any other engine is refused */
} engine_options;

enum { ENGINE_OK = 0, ENGINE_FAILED = 1, ENGINE_ABORTED = 2, ENGINE_BROKEN = 3 };

typedef struct engine engine;

/* NULL on failure, with the reason in err. */
engine *engine_open(const engine_options *options, char *err, size_t err_len);
/* One match: result gets ubc_run's twelve values (rounds, winner, end reason, dragons A/B, lengths A/B, events,
   and since unswbc 1.2.3 queen lengths A/B and longest dragons A/B; engine.py allocates 12 * 4 bytes for them).
   Returns ENGINE_OK; ENGINE_FAILED when the engine said why in err (engine.py's RuntimeError); ENGINE_ABORTED when
   a reply callback returned -1 (errno kept); ENGINE_BROKEN for a trap or runtime error (err says). */
int engine_run(engine *e, const engine_callbacks *callbacks, const uint8_t *map, size_t map_len, int32_t debug,
               uint64_t seed, int32_t result[12], char *err, size_t err_len);
/* The replay of the match just run, with the teams named `a` and `b`: *data stays valid until engine_close. */
int engine_replay(engine *e, const char *a, const char *b, const uint8_t **data, size_t *len, char *err,
                  size_t err_len);
void engine_close(engine *e);

#endif
