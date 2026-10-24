/* The few types and functions of wasmtime's C API (version 49.0.0) the judge uses, declared by hand so no headers need
   installing: copied from wasmtime v49.0.0's include/ (Apache-2.0 WITH LLVM-exception, (c) the Wasmtime Project
   Developers). Checked against those headers on 2026-09-28 (aarch64 macOS; Linux x86_64 and aarch64 share the LP64
   layout): each struct's size, alignment and field offsets, and every prototype in WASMTIME_FUNCTIONS with
   __builtin_types_compatible_p. The library itself is the one wasmtime-py ships in the toolkit's venv
   (wasmtime/<platform>-<machine>/_libwasmtime.*), loaded with dlopen, so the judge runs the very machine code the
   toolkit's games run. A different wasmtime needs these re-checked: native_judge.WASMTIME pins the version. */
#ifndef WASMTIME_MIN_H
#define WASMTIME_MIN_H

#include <stddef.h>
#include <stdint.h>

typedef struct wasm_engine_t wasm_engine_t;
typedef struct wasmtime_store wasmtime_store_t;
typedef struct wasmtime_context wasmtime_context_t;
typedef struct wasmtime_linker wasmtime_linker_t;
typedef struct wasmtime_module wasmtime_module_t;
typedef struct wasmtime_error wasmtime_error_t;
typedef struct wasm_trap_t wasm_trap_t;
typedef struct wasm_functype_t wasm_functype_t;
typedef struct wasm_valtype_t wasm_valtype_t;
typedef struct wasmtime_caller wasmtime_caller_t;
typedef struct wasi_config_t wasi_config_t;

typedef struct wasm_byte_vec_t {
    size_t size;
    char *data;
} wasm_byte_vec_t;

typedef struct wasm_valtype_vec_t {
    size_t size;
    wasm_valtype_t **data;
} wasm_valtype_vec_t;

enum { WASM_I32 = 0, WASM_I64 = 1 };
enum { WASMTIME_EXTERN_FUNC = 0, WASMTIME_EXTERN_MEMORY = 3 };

typedef struct wasmtime_func {
    uint64_t store_id;
    void *private_;
} wasmtime_func_t;

typedef struct wasmtime_memory {
    struct {
        uint64_t store_id;
        uint32_t private1;
    } inner; /* padded to 16 bytes, as in the real header's anonymous struct */
    uint32_t private2;
} wasmtime_memory_t;

typedef struct wasmtime_instance {
    uint64_t store_id;
    size_t private_;
} wasmtime_instance_t;

/* wasmtime_extern_union_t's largest member is 24 bytes (memory, table). */
typedef union wasmtime_extern_union {
    wasmtime_func_t func;
    wasmtime_memory_t memory;
    unsigned char bytes[24];
} wasmtime_extern_union_t;

typedef struct wasmtime_extern {
    uint8_t kind;
    wasmtime_extern_union_t of;
} wasmtime_extern_t;

typedef union wasmtime_val_raw {
    int32_t i32;
    int64_t i64;
    float f32;
    double f64;
    uint8_t v128[16];
    uint32_t anyref;
    uint32_t externref;
    uint32_t exnref;
    void *funcref;
} wasmtime_val_raw_t;

typedef wasm_trap_t *(*wasmtime_func_unchecked_callback_t)(void *env, wasmtime_caller_t *caller,
                                                           wasmtime_val_raw_t *args_and_results, size_t num);

/* Every function the judge calls, as (return type, name, parameters): engine_wasmtime.c resolves each with dlsym. */
#define WASMTIME_FUNCTIONS(X) \
    X(wasm_engine_t *, wasm_engine_new, (void)) \
    X(void, wasm_engine_delete, (wasm_engine_t *)) \
    X(wasmtime_error_t *, wasmtime_module_new, (wasm_engine_t *, const uint8_t *, size_t, wasmtime_module_t **)) \
    X(wasmtime_error_t *, wasmtime_module_deserialize, (wasm_engine_t *, const uint8_t *, size_t, wasmtime_module_t **)) \
    X(wasmtime_error_t *, wasmtime_module_serialize, (const wasmtime_module_t *, wasm_byte_vec_t *)) \
    X(void, wasmtime_module_delete, (wasmtime_module_t *)) \
    X(wasmtime_store_t *, wasmtime_store_new, (wasm_engine_t *, void *, void (*)(void *))) \
    X(wasmtime_context_t *, wasmtime_store_context, (wasmtime_store_t *)) \
    X(void, wasmtime_store_delete, (wasmtime_store_t *)) \
    X(wasi_config_t *, wasi_config_new, (void)) \
    X(void, wasi_config_set_stderr_custom, (wasi_config_t *, ptrdiff_t (*)(void *, const unsigned char *, size_t), \
                                           void *, void (*)(void *))) \
    X(wasmtime_error_t *, wasmtime_context_set_wasi, (wasmtime_context_t *, wasi_config_t *)) \
    X(wasmtime_linker_t *, wasmtime_linker_new, (wasm_engine_t *)) \
    X(void, wasmtime_linker_delete, (wasmtime_linker_t *)) \
    X(wasmtime_error_t *, wasmtime_linker_define_wasi, (wasmtime_linker_t *)) \
    X(wasmtime_error_t *, wasmtime_linker_define_func_unchecked, (wasmtime_linker_t *, const char *, size_t, \
                                                                  const char *, size_t, const wasm_functype_t *, \
                                                                  wasmtime_func_unchecked_callback_t, void *, \
                                                                  void (*)(void *))) \
    X(wasmtime_error_t *, wasmtime_linker_instantiate, (const wasmtime_linker_t *, wasmtime_context_t *, \
                                                        const wasmtime_module_t *, wasmtime_instance_t *, \
                                                        wasm_trap_t **)) \
    X(_Bool, wasmtime_instance_export_get, (wasmtime_context_t *, const wasmtime_instance_t *, const char *, size_t, \
                                            wasmtime_extern_t *)) \
    X(uint8_t *, wasmtime_memory_data, (wasmtime_context_t *, const wasmtime_memory_t *)) \
    X(size_t, wasmtime_memory_data_size, (const wasmtime_context_t *, const wasmtime_memory_t *)) \
    X(wasmtime_error_t *, wasmtime_func_call_unchecked, (wasmtime_context_t *, const wasmtime_func_t *, \
                                                         wasmtime_val_raw_t *, size_t, wasm_trap_t **)) \
    X(wasmtime_context_t *, wasmtime_caller_context, (wasmtime_caller_t *)) \
    X(wasm_valtype_t *, wasm_valtype_new, (uint8_t)) \
    X(void, wasm_valtype_vec_new, (wasm_valtype_vec_t *, size_t, wasm_valtype_t *const[])) \
    X(void, wasm_valtype_vec_new_empty, (wasm_valtype_vec_t *)) \
    X(wasm_functype_t *, wasm_functype_new, (wasm_valtype_vec_t *, wasm_valtype_vec_t *)) \
    X(void, wasm_functype_delete, (wasm_functype_t *)) \
    X(void, wasmtime_error_message, (const wasmtime_error_t *, wasm_byte_vec_t *)) \
    X(void, wasmtime_error_delete, (wasmtime_error_t *)) \
    X(wasm_trap_t *, wasmtime_trap_new, (const char *, size_t)) \
    X(void, wasm_trap_message, (const wasm_trap_t *, wasm_byte_vec_t *)) \
    X(void, wasm_trap_delete, (wasm_trap_t *)) \
    X(void, wasm_byte_vec_delete, (wasm_byte_vec_t *))

#endif
