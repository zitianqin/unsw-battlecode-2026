/* The native judge: plays one game of the organisers' engine (unswbc_engine.wasm, run as it is: judge_engine.h) between two
   native bots, doing in C what `unswbc run MAP A B -o REPLAY --seed N` does in Python for native bots, so the game,
   its replay bytes and the output the referee reads (referee/run.py's parse_game) are the same:

       native-judge --lib LIBWASMTIME --wasm WASM [--cwasm CWASM] [--shim SHIM] [--seed N] [--debug N]
                    [--cwd-a DIR] [--cwd-b DIR] -o REPLAY MAP NAME_A EXE_A NAME_B EXE_B

   NAME_A and NAME_B are the bots as `unswbc run` was given them (the replay names the teams with them), EXE_A and
   EXE_B their resolved executables (unswbc.run._resolve's argv[0]), and --cwd-a and --cwd-b the folders each team's
   processes run in (_resolve's second answer, the folder as given, not resolved; by default the executable's).
   --debug is the engine's debug mask, cli.py's `keep` (decimal; default 15, DEBUG_ALL; `unswbc run --no-debug` is 0,
   which leaves the bots' indicators, drawings and logs out of the replay).
   Mirrored from unswbc 1.2.7 (native_judge.KNOWN_TOOLKITS pins the files): run.py's execute (its printed lines,
   progress lines included, the seed and map-defect lines, the result and replay lines, and `error: ...` on stderr
   with exit 1 where the toolkit fails), engine.py's EngineModule.run, turn.py's Framer and TurnBot (a turn ends on
   ENDTURN, a park, an exit or the 10 s wall limit; the same restarts, skips and reasons) and bot.py's Pool and Bot
   (8 warm spares a team, a SOCK_SEQPACKET stdout where the system has one, the Linux stdin-park test, SIGPIPE and
   SIGXFSZ back to default in each bot). With --shim, each team's processes are forked from a server started with
   fork_server.py's shim, speaking its protocol (hello, 'S', 'K', 'W'), falling back as fork_server.py does and
   saying so with the same "fork server: ..." lines.

   Where it knowingly differs from `unswbc run`, none of which a referee game meets: the seed is decimal only (the
   referee's; cli.py's int(s, 0) also reads 0x, 0o, 0b, signs and underscores, which this refuses); colour follows
   NO_COLOR, TERM=dumb and FORCE_COLOR as color.py does, but a terminal alone neither colours nor redraws the progress
   line (the referee pipes the output); stdout is line-buffered, so in one merged stream (a host's) its lines keep
   their order with stderr's, where Python's block-buffered stdout can trail them; and its own failures (a bad
   argument, a missing library, a wasm or compiled engine it wasn't built for) have error lines the toolkit has no
   counterpart for. The error line itself goes out before stdout is flushed, as Python's does.

   The referee (native_judge.py) runs this only when switched on, for an engine, toolkit, judge and shim it was built
   and checked for (native_judge.key); the switch stays off until the orchestrator has read a clean
   referee/native_fidelity.py report. */
#if defined(__linux__)
#define _GNU_SOURCE
#endif
#include "judge_engine.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef ENGINE_SHA256
#error "build with -DENGINE_SHA256=\"<the engine's sha256>\" (native_judge.py build does)"
#endif

extern char **environ;

#define WARM_SPARES 8
#define CHUNK (1 << 20)
#define BUFFER_LIMIT (10 * 1024)
#define WALL_LIMIT_S 10.0
#define MAX_ROUNDS 500
#define DEBUG_ALL 15
#define START_S 10
#define REQUEST_S 30
static const char PARK[] = "\0UNSWBC PARK"; /* turn.py's PARK, 12 bytes */
#define PARK_LEN 12
#ifdef __APPLE__
#define PRELOAD "DYLD_INSERT_LIBRARIES"
#else
#define PRELOAD "LD_PRELOAD"
#endif
#define CONTROL "BC_FORK_SERVER_FD"

static uint8_t chunk_buffer[CHUNK];

/* color.py's ON (see the header): what paint() wraps a line in. */
static int colour;
#define YELLOW "\033[33m"
#define GREEN "\033[32m"
static const char *paint_on(const char *code) { return colour ? code : ""; }
static const char *paint_off(void) { return colour ? "\033[0m" : ""; }

/* Python's time.monotonic: mach_absolute_time on macOS (CLOCK_UPTIME_RAW, which stops while asleep), else
   CLOCK_MONOTONIC. */
static double now(void) {
    struct timespec t;
#ifdef __APPLE__
    clock_gettime(CLOCK_UPTIME_RAW, &t);
#else
    clock_gettime(CLOCK_MONOTONIC, &t);
#endif
    return (double)t.tv_sec + t.tv_nsec / 1e9;
}

static void *need(void *p) {
    if (!p) {
        fprintf(stderr, "error: out of memory\n");
        exit(1);
    }
    return p;
}

typedef struct bytes {
    uint8_t *data;
    size_t len, cap;
} bytes;

static void append(bytes *b, const void *data, size_t len) {
    if (!len) return;
    if (b->len + len > b->cap) {
        b->cap = (b->len + len) * 2 + 64;
        b->data = need(realloc(b->data, b->cap));
    }
    memcpy(b->data + b->len, data, len);
    b->len += len;
}

/* ---- Python's bytes.decode("utf-8", "replace").encode(): each maximal invalid subpart becomes U+FFFD ---- */

static void utf8_replace(const uint8_t *s, size_t n, bytes *out) {
    static const uint8_t FFFD[3] = {0xEF, 0xBF, 0xBD};
    size_t i = 0;
    while (i < n) {
        uint8_t c = s[i];
        if (c < 0x80) {
            append(out, &c, 1);
            i++;
            continue;
        }
        size_t need_len;
        uint8_t lo = 0x80, hi = 0xBF;
        if (c >= 0xC2 && c <= 0xDF)
            need_len = 2;
        else if (c >= 0xE0 && c <= 0xEF) {
            need_len = 3;
            if (c == 0xE0) lo = 0xA0;
            if (c == 0xED) hi = 0x9F;
        } else if (c >= 0xF0 && c <= 0xF4) {
            need_len = 4;
            if (c == 0xF0) lo = 0x90;
            if (c == 0xF4) hi = 0x8F;
        } else {
            append(out, FFFD, 3);
            i++;
            continue;
        }
        size_t k = 1;
        while (k < need_len && i + k < n) {
            uint8_t d = s[i + k];
            if (k == 1 ? (d < lo || d > hi) : (d < 0x80 || d > 0xBF)) break;
            k++;
        }
        if (k == need_len)
            append(out, s + i, k);
        else
            append(out, FFFD, 3);
        i += k;
    }
}

/* ---- turn.py's Framer ---- */

typedef struct framer {
    bytes out, line;
    int armed, done;
    long long park_at; /* -1: none (None or a tail that isn't digits: neither can equal a count written) */
} framer;

static void framer_arm(framer *f) {
    f->out.len = f->line.len = 0;
    f->armed = 1, f->done = 0, f->park_at = -1;
}

static int is_space(uint8_t c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f'; }

/* True at ENDTURN; the rest of that chunk is never seen. */
static int framer_feed(framer *f, const uint8_t *chunk, size_t n) {
    for (size_t i = 0; i < n; i++) {
        uint8_t byte = chunk[i];
        if (byte != '\n') {
            if (f->line.len < BUFFER_LIMIT) append(&f->line, &byte, 1);
            continue;
        }
        const uint8_t *line = f->line.data;
        size_t len = f->line.len;
        if (len && line[len - 1] == '\r') len--;
        f->line.len = 0; /* the data stays readable until the next append */
        if (len == 5 && memcmp(line, "READY", 5) == 0) continue;
        if (len >= PARK_LEN && memcmp(line, PARK, PARK_LEN) == 0) {
            const uint8_t *tail = line + PARK_LEN, *end = line + len;
            while (tail < end && is_space(*tail)) tail++;
            while (end > tail && is_space(end[-1])) end--;
            long long value = end > tail ? 0 : -1;
            for (const uint8_t *c = tail; c < end && value >= 0; c++)
                value = *c >= '0' && *c <= '9' && value <= (LLONG_MAX - 9) / 10 ? value * 10 + (*c - '0') : -1;
            f->park_at = value;
            continue;
        }
        if (!f->armed) continue;
        if (len == 7 && memcmp(line, "ENDTURN", 7) == 0) {
            f->armed = 0, f->done = 1;
            return 1;
        }
        if (f->out.len < BUFFER_LIMIT) {
            size_t room = BUFFER_LIMIT - f->out.len;
            append(&f->out, line, len < room ? len : room);
            if (f->out.len < BUFFER_LIMIT) append(&f->out, "\n", 1);
        }
    }
    return 0;
}

static void framer_take(framer *f, bytes *reply) {
    if (f->out.len < BUFFER_LIMIT && !f->done) {
        size_t room = BUFFER_LIMIT - f->out.len;
        append(&f->out, f->line.data, f->line.len < room ? f->line.len : room);
    }
    f->line.len = 0;
    reply->len = 0;
    utf8_replace(f->out.data, f->out.len, reply);
    f->out.len = 0;
}

/* ---- saying things once (fork_server.say_once) ---- */

static char *said[16];
static int said_count;

static void say_once(const char *format, ...) {
    char text[1024];
    va_list args;
    va_start(args, format);
    vsnprintf(text, sizeof text, format, args);
    va_end(args);
    for (int i = 0; i < said_count; i++)
        if (strcmp(said[i], text) == 0) return;
    if (said_count < 16) said[said_count++] = need(strdup(text));
    fprintf(stderr, "fork server: %s\n", text);
}

/* ---- bot processes: bot.py's Pool, and fork_server.py's Server and Child ---- */

typedef struct server {
    int sock; /* -1: none */
    pid_t pid;
    int broken;
} server;

typedef struct proc {
    pid_t pid;
    int in, out, err; /* stdin to write, stdout and stderr to read; -1 once closed */
    server *via;      /* the fork server that started it, or NULL */
    int reaped, code; /* code as Popen.returncode gives it (-signal when killed) */
} proc;

typedef struct pool {
    const char *exe, *cwd;
    char name[256]; /* Path(exe).name */
    proc spares[WARM_SPARES];
    int count;
    server srv;
    int forking;
} pool;

static int cloexec(int fd) { return fcntl(fd, F_SETFD, FD_CLOEXEC); }

static int status_code(int status) { return WIFSIGNALED(status) ? -WTERMSIG(status) : WEXITSTATUS(status); }

static pid_t reap(pid_t pid, int options, int *status) {
    pid_t got;
    do got = waitpid(pid, status, options);
    while (got < 0 && errno == EINTR);
    return got;
}

/* posix_spawn of argv[0] in cwd with stdin/stdout/stderr as given (-1: /dev/null), SIGPIPE and SIGXFSZ at their
   defaults (Popen's restore_signals), no other descriptor but `keep` (-1: none). 0, or an errno. */
static int spawn(const char *exe, const char *cwd, int in, int out, int err, int keep, char **env, pid_t *pid) {
    posix_spawn_file_actions_t actions;
    posix_spawnattr_t attr;
    posix_spawn_file_actions_init(&actions);
    posix_spawnattr_init(&attr);
    int fds[3] = {in, out, err};
    for (int i = 0; i < 3; i++) {
        if (fds[i] >= 0)
            posix_spawn_file_actions_adddup2(&actions, fds[i], i);
        else
            posix_spawn_file_actions_addopen(&actions, i, "/dev/null", i ? O_WRONLY : O_RDONLY, 0);
    }
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    posix_spawn_file_actions_addchdir_np(&actions, cwd); /* macOS 26 renamed it; the _np name stays for older ones */
#pragma clang diagnostic pop
    sigset_t defaults, empty;
    sigemptyset(&defaults);
    sigaddset(&defaults, SIGPIPE);
    sigaddset(&defaults, SIGXFSZ);
    sigemptyset(&empty);
    posix_spawnattr_setsigdefault(&attr, &defaults);
    posix_spawnattr_setsigmask(&attr, &empty);
    short flags = POSIX_SPAWN_SETSIGDEF | POSIX_SPAWN_SETSIGMASK;
#ifdef __APPLE__
    flags |= POSIX_SPAWN_CLOEXEC_DEFAULT; /* close_fds: only 0-2 (and `keep`) reach the bot */
    if (keep >= 0) posix_spawn_file_actions_addinherit_np(&actions, keep);
#else
    (void)keep; /* every descriptor of ours but `keep` is close-on-exec */
#endif
    posix_spawnattr_setflags(&attr, flags);
    char *argv[] = {(char *)exe, NULL};
    int failed = posix_spawn(pid, exe, &actions, &attr, argv, env);
    posix_spawn_file_actions_destroy(&actions);
    posix_spawnattr_destroy(&attr);
    return failed;
}

static int send_all(int fd, const void *data, size_t len) {
    const char *at = data;
    while (len) {
        ssize_t put = send(fd, at, len, 0);
        if (put < 0 && errno == EINTR) continue;
        if (put <= 0) return -1;
        at += put, len -= (size_t)put;
    }
    return 0;
}

/* The server's three int32s; -1 with errno (ETIMEDOUT for a timeout, EPIPE for a closed socket). */
static int receive(server *s, int32_t message[3]) {
    size_t have = 0;
    while (have < 12) {
        ssize_t got = recv(s->sock, (char *)message + have, 12 - have, 0);
        if (got < 0 && errno == EINTR) continue;
        if (got < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) errno = ETIMEDOUT;
        if (got == 0) errno = EPIPE;
        if (got <= 0) return -1;
        have += (size_t)got;
    }
    return 0;
}

static void set_timeout(int sock, int seconds) {
    struct timeval t = {seconds, 0};
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &t, sizeof t);
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &t, sizeof t);
}

/* What ServerGone says: Python's socket.timeout reads "timed out", a closed socket "it closed its socket". */
static const char *gone_text(int error) {
    if (error == ETIMEDOUT) return "timed out";
    if (error == EPIPE) return "it closed its socket";
    return strerror(error);
}

static void server_close(server *s) {
    if (s->sock < 0) return;
    close(s->sock);
    s->sock = -1;
    int status;
    for (int waited = 0; waited < START_S * 100; waited++) {
        if (reap(s->pid, WNOHANG, &status) == s->pid) return;
        usleep(10000);
    }
    kill(s->pid, SIGKILL);
    reap(s->pid, 0, &status);
}

/* fork_server.Server: 0, or -1 with why in `why`. */
static int server_start(server *s, const char *exe, const char *cwd, const char *shim, char *why, size_t n) {
    int pair[2];
    s->sock = -1, s->broken = 0;
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, pair) != 0) {
        snprintf(why, n, "[Errno %d] %s", errno, strerror(errno));
        return -1;
    }
    cloexec(pair[0]);
    size_t count = 0;
    while (environ[count]) count++;
    char **env = need(calloc(count + 3, sizeof *env));
    size_t used = 0;
    for (size_t i = 0; i < count; i++)
        if (strncmp(environ[i], CONTROL "=", sizeof CONTROL) && strncmp(environ[i], PRELOAD "=", sizeof PRELOAD))
            env[used++] = environ[i];
    char control[64], preload[4200];
    snprintf(control, sizeof control, CONTROL "=%d", pair[1]);
    snprintf(preload, sizeof preload, PRELOAD "=%s", shim);
    env[used++] = control, env[used++] = preload;
    int failed = spawn(exe, cwd, -1, -1, -1, pair[1], env, &s->pid);
    free(env);
    close(pair[1]);
    if (failed) {
        close(pair[0]);
        snprintf(why, n, "[Errno %d] %s: '%s'", failed, strerror(failed), exe);
        return -1;
    }
    s->sock = pair[0];
    set_timeout(s->sock, START_S);
    int32_t hello[3];
    if (receive(s, hello) != 0 || hello[0] != 1 || hello[1] != s->pid) {
        const char *base = strrchr(exe, '/') ? strrchr(exe, '/') + 1 : exe;
        char reason[256];
        if (errno == ETIMEDOUT || errno == EPIPE)
            snprintf(reason, sizeof reason, "%s", gone_text(errno));
        else
            snprintf(reason, sizeof reason, "it answered (%d, %d) to hello", hello[0], hello[1]);
        snprintf(why, n, "the shim didn't start in %s (%s)", base, reason);
        server_close(s);
        return -1;
    }
    set_timeout(s->sock, REQUEST_S);
    return 0;
}

/* One request: 0 with the reply, or -1 with the server marked broken (asked nothing more: a late reply would be
   read as the next one's). */
static int ask_server(server *s, char op, int32_t pid, int32_t timeout_ms, const int *fds, int32_t reply[3]) {
    if (s->broken || s->sock < 0) return -1;
    int32_t message[3] = {op, pid, timeout_ms};
    int ok;
    if (fds) {
        union {
            struct cmsghdr header;
            char space[CMSG_SPACE(3 * sizeof(int))];
        } control;
        memset(&control, 0, sizeof control);
        struct iovec part = {message, sizeof message};
        struct msghdr m;
        memset(&m, 0, sizeof m);
        m.msg_iov = &part, m.msg_iovlen = 1;
        m.msg_control = control.space, m.msg_controllen = sizeof control.space;
        struct cmsghdr *c = CMSG_FIRSTHDR(&m);
        c->cmsg_level = SOL_SOCKET, c->cmsg_type = SCM_RIGHTS, c->cmsg_len = CMSG_LEN(3 * sizeof(int));
        memcpy(CMSG_DATA(c), fds, 3 * sizeof(int));
        ssize_t sent;
        do sent = sendmsg(s->sock, &m, 0);
        while (sent < 0 && errno == EINTR);
        ok = sent == (ssize_t)sizeof message || (sent > 0 && send_all(s->sock, (char *)message + sent,
                                                                        sizeof message - (size_t)sent) == 0);
    } else
        ok = send_all(s->sock, message, sizeof message) == 0;
    if (!ok || receive(s, reply) != 0) {
        s->broken = errno ? errno : EPIPE;
        return -1;
    }
    return 0;
}

/* One bot process (Pool._launch, or fork_server.launch): 0, or -1 with errno set to the OS error the toolkit would
   raise and `exec_error` set when it names the file. */
static int launch(pool *p, proc *out, int *exec_error) {
    int in[2], err[2], stdout_pair[2];
    *exec_error = 0;
    if (pipe(in) != 0) return -1;
    if (pipe(err) != 0) {
        close(in[0]), close(in[1]);
        return -1;
    }
#ifdef __linux__
    int seqpacket = socketpair(AF_UNIX, SOCK_SEQPACKET, 0, stdout_pair) == 0;
#else
    int seqpacket = 0;
#endif
    if (!seqpacket && pipe(stdout_pair) != 0) {
        close(in[0]), close(in[1]), close(err[0]), close(err[1]);
        return -1;
    }
    int theirs_out = stdout_pair[1], ours_out = stdout_pair[0];
    int all[6] = {in[0], in[1], err[0], err[1], stdout_pair[0], stdout_pair[1]};
    for (int i = 0; i < 6; i++) cloexec(all[i]);
    int failed = 0;
    memset(out, 0, sizeof *out);
    if (p->forking) {
        int fds[3] = {in[0], theirs_out, err[1]};
        int32_t reply[3];
        if (ask_server(&p->srv, 'S', 0, 0, fds, reply) == 0) {
            if (reply[0] == 1) {
                out->pid = reply[1];
                out->via = &p->srv;
            } else
                failed = -reply[0];
        } else {
            p->forking = 0;
            say_once("stopped for %s (%s); its processes start as usual from here", p->name,
                     gone_text(p->srv.broken));
        }
    }
    if (!failed && !out->via) {
        failed = spawn(p->exe, p->cwd, in[0], theirs_out, err[1], -1, environ, &out->pid);
        if (failed == ENOENT || failed == EACCES || failed == ENOEXEC || failed == ENOTDIR) *exec_error = 1;
    }
    close(in[0]), close(theirs_out), close(err[1]);
    if (failed) {
        close(in[1]), close(ours_out), close(err[0]);
        errno = failed;
        return -1;
    }
    out->in = in[1], out->out = ours_out, out->err = err[0];
    return 0;
}

static void proc_close_fds(proc *c) {
    int *fds[3] = {&c->in, &c->out, &c->err};
    for (int i = 0; i < 3; i++)
        if (*fds[i] >= 0) close(*fds[i]), *fds[i] = -1;
}

/* Popen.kill then wait, or Child.kill (its server gone: the process is told to end by the end of its input). */
static void proc_stop(proc *c) {
    if (!c->reaped) {
        if (c->via) {
            int32_t reply[3];
            if (ask_server(c->via, 'K', c->pid, 0, NULL, reply) == 0)
                c->code = reply[0] == 1 ? status_code(reply[2]) : -9;
            else
                c->code = -9;
        } else {
            int status;
            kill(c->pid, SIGKILL);
            c->code = reap(c->pid, 0, &status) == c->pid ? status_code(status) : -9;
        }
        c->reaped = 1;
    }
    proc_close_fds(c);
}

/* Popen.wait(timeout): 1 with c->code once it has exited, else 0. */
static int proc_wait(proc *c, int timeout_ms) {
    if (c->reaped) return 1;
    int status;
    if (c->via) {
        int32_t reply[3];
        if (ask_server(c->via, 'W', c->pid, timeout_ms, NULL, reply) != 0) {
            c->reaped = 1, c->code = -9; /* its server went and took it (or left it) */
            return 1;
        }
        if (reply[0] != 1) return 0;
        c->reaped = 1, c->code = status_code(reply[2]);
        return 1;
    }
    for (int waited = 0;; waited++) {
        pid_t got = reap(c->pid, WNOHANG, &status);
        if (got == c->pid) {
            c->reaped = 1, c->code = status_code(status);
            return 1;
        }
        if (got < 0 || waited >= timeout_ms) return 0;
        usleep(1000);
    }
}

static char os_error[512]; /* the toolkit's `error: ...` text for an OS error that ends the game */

static void note_os_error(int error, int exec_error, const char *exe) {
    if (exec_error)
        snprintf(os_error, sizeof os_error, "[Errno %d] %s: '%s'", error, strerror(error), exe);
    else
        snprintf(os_error, sizeof os_error, "[Errno %d] %s", error, strerror(error));
}

static int pool_fill(pool *p) {
    while (p->count < WARM_SPARES) {
        int exec_error;
        if (launch(p, &p->spares[p->count], &exec_error) != 0) {
            note_os_error(errno, exec_error, p->exe);
            return -1;
        }
        p->count++;
    }
    return 0;
}

/* `folder`: where its processes run (run.py's botdir), or NULL for the executable's folder. */
static int pool_open(pool *p, const char *exe, const char *folder, const char *shim) {
    memset(p, 0, sizeof *p);
    p->exe = exe;
    char *cwd = need(strdup(folder ? folder : exe));
    char *slash = strrchr(cwd, '/');
    if (folder)
        slash = NULL; /* as given */
    else if (slash == cwd)
        slash[1] = '\0';
    else if (slash)
        *slash = '\0';
    else
        strcpy(cwd, ".");
    p->cwd = cwd;
    snprintf(p->name, sizeof p->name, "%s", strrchr(exe, '/') ? strrchr(exe, '/') + 1 : exe);
    p->srv.sock = -1;
    if (shim) {
        char why[1024];
        if (server_start(&p->srv, exe, cwd, shim, why, sizeof why) == 0)
            p->forking = 1;
        else
            say_once("off for %s (%s); its processes start as usual", p->name, why);
    }
    return pool_fill(p);
}

static int pool_take(pool *p, proc *out) {
    if (p->count) {
        *out = p->spares[--p->count];
    } else {
        int exec_error;
        if (launch(p, out, &exec_error) != 0) {
            note_os_error(errno, exec_error, p->exe);
            return -1;
        }
    }
    return pool_fill(p);
}

static void pool_close(pool *p) {
    for (int i = 0; i < p->count; i++) proc_stop(&p->spares[i]);
    p->count = 0;
    server_close(&p->srv);
}

/* ---- turn.py's TurnBot with bot.py's Bot ---- */

typedef struct bot {
    int live;
    char team; /* 'A', 'B', or another letter the init named */
    bytes init;
    framer fr;
    int activate, is_new, has_process, parks;
    long long written;
    proc process;
    const char *error;
    char error_text[64];
} bot;

static pool pools[2];

static pool *pool_of(bot *b) { return &pools[b->team == 'B']; }

static void bot_stop(bot *b) {
    if (!b->has_process) return;
    proc_stop(&b->process);
    b->has_process = 0;
}

static int bot_start(bot *b) {
    bot_stop(b);
    if (pool_take(pool_of(b), &b->process) != 0) return -1;
    b->has_process = 1;
    b->activate = 0; /* only the zygote's Python bots are activated */
    for (;;) {       /* whatever it wrote before its first turn is never read */
        struct pollfd one = {b->process.out, POLLIN, 0};
        if (poll(&one, 1, 0) <= 0) break;
        ssize_t got = read(b->process.out, chunk_buffer, CHUNK);
        if (got <= 0) break;
    }
    return 0;
}

static int bot_fresh(bot *b) {
    if (bot_start(b) != 0) return -1;
    b->written = 0, b->is_new = 1;
    return 0;
}

static const char *bot_skip(bot *b, const char *reason, bytes *reply) {
    snprintf(b->error_text, sizeof b->error_text, "%s", reason);
    b->error = b->error_text;
    bot_stop(b);
    reply->len = 0;
    return NULL;
}

static const char *bot_reason(bot *b, char *text, size_t n) {
    if (b->has_process && proc_wait(&b->process, 50))
        snprintf(text, n, "exited with code %d", b->process.code);
    else
        snprintf(text, n, "exited");
    return text;
}

static int bot_write(bot *b, const uint8_t *data, size_t len) {
    while (len) {
        ssize_t put = write(b->process.in, data, len);
        if (put < 0 && errno == EINTR) continue;
        if (put <= 0) return 0;
        data += put, len -= (size_t)put;
    }
    return 1;
}

/* bot.py's _parked_on_stdin: on Linux, the bot blocked in read(0) with nothing left in its stdin. */
static int parked_on_stdin(bot *b) {
#ifdef __linux__
    int pending = 0;
    if (ioctl(b->process.in, FIONREAD, &pending) != 0 || pending) return 0;
    char path[64], text[256];
    snprintf(path, sizeof path, "/proc/%d/syscall", (int)b->process.pid);
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    ssize_t got = read(fd, text, sizeof text - 1);
    close(fd);
    if (got <= 0) return 0;
    text[got] = '\0';
    char first[32], second[32];
    if (sscanf(text, "%31s %31s", first, second) != 2) return 0;
    char *rest = strchr(text, ' ') ? strchr(strchr(text, ' ') + 1, ' ') : NULL;
    if (!rest) return 0; /* fewer than three fields */
    return (strcmp(first, "0") == 0 || strcmp(first, "63") == 0) && strcmp(second, "0x0") == 0;
#else
    (void)b;
    return 0;
#endif
}

enum { WAIT, DATA, EXIT, PARKED };

/* Bot._poll: stderr drained and dropped (nothing verbose here), then stdout read for up to `timeout` seconds. */
static int bot_poll(bot *b, double timeout) {
    proc *c = &b->process;
#ifdef __linux__
    double slice = timeout < 0.005 ? timeout : 0.005; /* the park test runs between reads, as bot.py's does */
#else
    double slice = timeout;
#endif
    double until = now() + slice;
    for (;;) {
        struct pollfd fds[2] = {{c->out, POLLIN, 0}, {c->err, POLLIN, 0}};
        double left = until - now();
        int ms = left > 0 ? (int)(left * 1000 + 0.999) : 0;
        int ready = poll(fds, c->err >= 0 ? 2 : 1, ms);
        if (ready < 0 && errno == EINTR) continue;
        if (ready > 0 && c->err >= 0 && fds[1].revents) {
            ssize_t got = read(c->err, chunk_buffer, CHUNK);
            if (got <= 0) close(c->err), c->err = -1;
        }
        if (ready > 0 && fds[0].revents) {
            ssize_t got = read(c->out, chunk_buffer, CHUNK);
            if (got <= 0) return EXIT;
            b->parks = 0;
            framer_feed(&b->fr, chunk_buffer, (size_t)got);
            return DATA;
        }
        if (ready <= 0 || now() >= until) break;
    }
    b->parks = parked_on_stdin(b) ? b->parks + 1 : 0;
    return b->parks > 1 ? PARKED : WAIT;
}

/* TurnBot.ask: the reply, in `reply`; b->error says why a turn failed. -1 on an OS error that ends the game. */
static int bot_ask(bot *b, const uint8_t *block, size_t len, bytes *reply) {
    static bytes data;
    char reason[64];
    b->error = NULL;
    reply->len = 0;
    if (!b->has_process && bot_fresh(b) != 0) return -1;
    for (int attempt = 0; attempt < 2; attempt++) {
        framer_arm(&b->fr);
        data.len = 0;
        if (b->activate) append(&data, "R", 1);
        if (b->is_new) append(&data, b->init.data, b->init.len);
        append(&data, block, len);
        size_t whole = (b->is_new ? b->init.len : 0) + len;
        const uint8_t *tail = data.data + data.len - (whole < 2 ? whole : 2);
        if (!(whole >= 2 && tail[0] == '\n' && tail[1] == '\n')) append(&data, "\n", 1);
        if (!bot_write(b, data.data, data.len)) {
            if (attempt == 0) {
                if (bot_fresh(b) != 0) return -1;
                continue;
            }
            bot_skip(b, bot_reason(b, reason, sizeof reason), reply);
            return 0;
        }
        b->written += (long long)data.len;
        b->activate = b->is_new = 0;
        double deadline = now() + WALL_LIMIT_S;
        for (;;) {
            double left = deadline - now();
            int state = bot_poll(b, left > 0 ? left : 0);
            if (b->fr.done || b->fr.park_at == b->written || state == PARKED) {
                framer_take(&b->fr, reply);
                return 0;
            }
            if (state == EXIT) break;
            if (now() >= deadline) {
                bot_skip(b, "ran out of time", reply);
                return 0;
            }
        }
        bot_reason(b, reason, sizeof reason);
        if (attempt || b->fr.out.len || b->fr.line.len) {
            bot_skip(b, reason, reply);
            return 0;
        }
        if (bot_fresh(b) != 0) return -1;
    }
    reply->len = 0;
    return 0;
}

/* ---- run.py's execute ---- */

static bot *bots;
static size_t bot_cap;
static bytes reply_buffer;

static bot *bot_at(int32_t id) {
    if (id < 0) return NULL;
    if ((size_t)id >= bot_cap) {
        size_t cap = (size_t)id * 2 + 16;
        bots = need(realloc(bots, cap * sizeof *bots));
        memset(bots + bot_cap, 0, (cap - bot_cap) * sizeof *bots);
        bot_cap = cap;
    }
    return &bots[id];
}

static struct {
    int round;
    double start, round_start, last;
} progress;

static void elapsed(double seconds, char *out, size_t n) {
    if (seconds < 60)
        snprintf(out, n, "%.1fs", seconds);
    else
        snprintf(out, n, "%dm%02ds", (int)seconds / 60, (int)seconds % 60);
}

static void round_ms(double seconds, char *out, size_t n) {
    if (seconds < 1)
        snprintf(out, n, "%dms", (int)(seconds * 1000));
    else
        snprintf(out, n, "%.1fs", seconds);
}

static void progress_update(int round, int a, int b) {
    if (round == progress.round) return;
    double t = now();
    progress.last = t - progress.round_start;
    progress.round_start = t;
    progress.round = round;
    double eta = round ? (t - progress.start) / round * (MAX_ROUNDS - round) : 0.0;
    if (round == 1 || round % 50 == 0 || round == MAX_ROUNDS) {
        char last[32], left[32];
        round_ms(progress.last, last, sizeof last);
        elapsed(eta, left, sizeof left);
        printf("running round %d/%d --  last: %s  eta: %s  dragons: %d vs %d\n", round + 1, MAX_ROUNDS, last, left,
               a, b);
    }
}

/* The first line of a block split on whitespace: its first two words, if any. */
static int head_words(const uint8_t *block, size_t len, char *first, char *second, size_t n) {
    const uint8_t *end = memchr(block, '\n', len);
    size_t line = end ? (size_t)(end - block) : len;
    int words = 0;
    size_t i = 0;
    first[0] = second[0] = '\0';
    while (i < line) {
        while (i < line && is_space(block[i])) i++;
        size_t start = i;
        while (i < line && !is_space(block[i])) i++;
        if (i > start) {
            if (words < 2) snprintf(words ? second : first, n, "%.*s", (int)(i - start), block + start);
            words++;
        }
    }
    return words;
}

static void who(int32_t id, const uint8_t *block, size_t len, char *out, size_t n) {
    char first[64], second[64];
    int words = head_words(block, len, first, second, sizeof first);
    bot *b = bot_at(id);
    char team = b && b->team ? b->team : '?';
    snprintf(out, n, "round %s: bot %d (team %c)", words > 1 && strcmp(first, "ROUND") == 0 ? second : "?", id, team);
}

static long turns;

static int on_reply(void *ctx, int32_t id, const uint8_t *block, size_t len, const uint8_t **out, size_t *out_len) {
    (void)ctx;
    turns++;
    char first[64], second[64];
    if (head_words(block, len, first, second, sizeof first) > 1 && strcmp(first, "ROUND") == 0) {
        int alive[2] = {0, 0};
        for (size_t i = 0; i < bot_cap; i++)
            if (bots[i].live) alive[bots[i].team == 'B']++;
        progress_update(atoi(second), alive[0], alive[1]);
    }
    bot *b = bot_at(id);
    if (!b || !b->live) {
        snprintf(os_error, sizeof os_error, "no bot for dragon %d", id);
        errno = 0;
        return -1;
    }
    if (bot_ask(b, block, len, &reply_buffer) != 0) return -1;
    if (b->error) {
        char text[128];
        who(id, block, len, text, sizeof text);
        printf("%s%s %s%s\n", paint_on(YELLOW), text, b->error, paint_off());
    }
    *out = reply_buffer.data;
    *out_len = reply_buffer.len;
    return 0;
}

static void on_spawn(void *ctx, int32_t id, const uint8_t *init, size_t len) {
    (void)ctx;
    bot *b = bot_at(id);
    if (!b) return;
    if (b->live) bot_stop(b);
    free(b->init.data);
    free(b->fr.out.data);
    free(b->fr.line.data);
    memset(b, 0, sizeof *b);
    b->live = 1;
    b->team = 'A';
    for (size_t at = 0; at < len;) { /* the first line starting TEAM names it (run.py's spawn) */
        const uint8_t *end = memchr(init + at, '\n', len - at);
        size_t line = end ? (size_t)(end - init) - at : len - at;
        char first[64], second[64];
        if (line >= 4 && memcmp(init + at, "TEAM", 4) == 0) {
            if (head_words(init + at, line, first, second, sizeof first) > 1) b->team = second[0];
            break;
        }
        at += line + 1;
    }
    append(&b->init, init, len);
}

static const char *death_reason(int reason) {
    switch (reason) {
    case 'W': return "hit a wall";
    case 'S': return "hit itself";
    case 'O': return "hit another dragon";
    case 'H': return "lost a head-to-head";
    case 'A': return "no valid action";
    default: return "died";
    }
}

static void on_death(void *ctx, int32_t id, int32_t round, int reason) {
    (void)ctx;
    bot *b = bot_at(id);
    printf("round %d: bot %d (team %c) died: %s\n", round, id, b && b->team ? b->team : '?',
           death_reason((char)reason));
    if (b && b->live) {
        bot_stop(b);
        b->live = 0;
    }
}

static int notices, notice_partial;

static void on_notice(void *ctx, const uint8_t *text, size_t len) {
    (void)ctx;
    for (size_t i = 0; i < len; i++) {
        if (text[i] == '\n')
            notices++, notice_partial = 0;
        else
            notice_partial = 1;
    }
}

/* run.py's _verdict (unswbc 1.2.3+): why the game ended, from ubc_run's values (winner [1], end reason [2], total
   lengths [5]/[6], queens [8]/[9], longest dragons [10]/[11]). */
static void verdict(const int32_t *r, char *out, size_t n) {
    if (r[2] == 0) {
        snprintf(out, n, "%s", r[1] ? "by elimination" : "both teams eliminated");
        return;
    }
    int32_t a[3] = {r[8], r[10], r[5]}, b[3] = {r[9], r[11], r[6]};
    if (r[1] == 0) {
        snprintf(out, n, "equal length: queen %d, longest %d, total %d each", a[0], a[1], a[2]);
        return;
    }
    const int32_t *won = r[1] == 1 ? a : b, *lost = r[1] == 1 ? b : a;
    static const char *labels[3] = {"longer queen", "longest dragon", "total length"};
    for (int i = 0; i < 3; i++)
        if (won[i] != lost[i]) {
            snprintf(out, n, "%s, %d to %d", labels[i], won[i], lost[i]);
            return;
        }
    snprintf(out, n, "on length");
}

static void summarise(void) {
    if (notices) printf("%smap: %d defect%s found and corrected -- rerun with -v to see them%s\n", paint_on(YELLOW),
                        notices, notices == 1 ? "" : "s", paint_off());
}

static void cleanup(void) {
    for (size_t i = 0; i < bot_cap; i++)
        if (bots[i].live) bot_stop(&bots[i]);
    for (int t = 0; t < 2; t++) pool_close(&pools[t]);
}

/* errors.fail: the error line on stderr, then (at exit, as Python's) whatever stdout still holds. */
static int fail(const char *format, ...) {
    va_list args;
    va_start(args, format);
    fprintf(stderr, "error: ");
    vfprintf(stderr, format, args);
    fprintf(stderr, "\n");
    va_end(args);
    fflush(stderr);
    fflush(stdout);
    return 1;
}

static int usage(void) {
    fprintf(stderr, "usage: native-judge --lib LIBWASMTIME --wasm WASM [--cwasm CWASM] [--shim SHIM] [--seed N] "
                    "[--debug N] [--cwd-a DIR] [--cwd-b DIR] -o REPLAY MAP NAME_A EXE_A NAME_B EXE_B\n");
    return 2;
}

static int mkdirs(char *path) {
    for (char *c = path + 1; *c; c++) {
        if (*c != '/') continue;
        *c = '\0';
        int ok = mkdir(path, 0777) == 0 || errno == EEXIST;
        *c = '/';
        if (!ok) return -1;
    }
    return mkdir(path, 0777) == 0 || errno == EEXIST ? 0 : -1;
}

static int write_replay(const char *path, const uint8_t *data, size_t len) {
    char parent[4096];
    snprintf(parent, sizeof parent, "%s", path);
    char *slash = strrchr(parent, '/');
    if (slash && slash != parent) {
        *slash = '\0';
        if (mkdirs(parent) != 0) return -1;
    }
    FILE *f = fopen(path, "wb");
    if (!f) return -1;
    int ok = fwrite(data, 1, len, f) == len;
    int saved = errno;
    ok = fclose(f) == 0 && ok;
    if (!ok && !errno) errno = saved;
    return ok ? 0 : -1;
}

/* Reads stdin and writes it as a turn's reply would be passed on (framer_take's UTF-8 repair): tests only. */
static int utf8_check(void) {
    bytes in = {0}, out = {0};
    ssize_t got;
    while ((got = read(0, chunk_buffer, CHUNK)) > 0) append(&in, chunk_buffer, (size_t)got);
    utf8_replace(in.data, in.len, &out);
    return fwrite(out.data, 1, out.len, stdout) == out.len ? 0 : 1;
}

int main(int argc, char **argv) {
    const char *lib = NULL, *wasm = NULL, *cwasm = NULL, *shim = NULL, *replay = NULL, *seed_text = NULL,
               *debug_text = NULL;
    const char *cwds[2] = {NULL, NULL};
    const char *positional[5];
    int count = 0;
    for (int i = 1; i < argc; i++) {
        const char *arg = argv[i];
        const char **into = strcmp(arg, "--lib") == 0 ? &lib : strcmp(arg, "--wasm") == 0 ? &wasm
                          : strcmp(arg, "--cwasm") == 0 ? &cwasm : strcmp(arg, "--shim") == 0 ? &shim
                          : strcmp(arg, "--seed") == 0 ? &seed_text : strcmp(arg, "--debug") == 0 ? &debug_text
                          : strcmp(arg, "-o") == 0 ? &replay
                          : strcmp(arg, "--cwd-a") == 0 ? &cwds[0] : strcmp(arg, "--cwd-b") == 0 ? &cwds[1] : NULL;
        if (strcmp(arg, "--utf8-check") == 0) return utf8_check();
        if (strcmp(arg, "--engine") == 0) {
            puts(ENGINE_SHA256);
            return 0;
        }
        if (into) {
            if (++i == argc) return usage();
            *into = argv[i];
        } else if (count < 5)
            positional[count++] = arg;
        else
            return usage();
    }
    if (count != 5 || !replay) return usage(); /* --lib and --wasm: engine_wasmtime.c needs them, engine_w2c.c not */
    int32_t debug = DEBUG_ALL;
    if (debug_text) {
        size_t n = strlen(debug_text);
        if (!n || n > 2 || strspn(debug_text, "0123456789") != n || (debug = atoi(debug_text)) > 31) return usage();
    }
    const char *map_arg = positional[0], *names[2] = {positional[1], positional[3]};
    const char *exes[2] = {positional[2], positional[4]};
    setvbuf(stdout, NULL, _IOLBF, 0);
    signal(SIGPIPE, SIG_IGN);
    const char *term = getenv("TERM"), *force = getenv("FORCE_COLOR");
    colour = !getenv("NO_COLOR") && !(term && strcmp(term, "dumb") == 0) && force && *force;
    fprintf(stderr, "native judge: playing (engine %.16s)\n", ENGINE_SHA256); /* what native_fidelity.py looks for */

    FILE *map_file = fopen(map_arg, "rb");
    if (!map_file) return fail("not found: %s", map_arg);
    bytes map = {0};
    size_t got;
    while ((got = fread(chunk_buffer, 1, CHUNK, map_file)) > 0) append(&map, chunk_buffer, got);
    fclose(map_file);

    printf("loaded c/c++ vs c/c++\n");
    uint64_t seed;
    if (seed_text) { /* decimal only, as int(s, 0) reads it: no sign, no leading zero, below 2^64 */
        size_t n = strlen(seed_text);
        int decimal = n && strspn(seed_text, "0123456789") == n && (seed_text[0] != '0' || n == 1);
        errno = 0;
        seed = decimal ? strtoull(seed_text, NULL, 10) : 0;
        if (!decimal || errno) return fail("bad seed (the native judge takes a decimal below 2^64): %s", seed_text);
        printf("%swithout --sandbox the seed repeats the pearls but not the bots' own random numbers%s\n",
               paint_on(YELLOW), paint_off());
    } else {
        seed = 0;
        int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
        if (fd < 0 || read(fd, &seed, sizeof seed) != (ssize_t)sizeof seed) return fail("no random seed");
        close(fd);
    }
    printf("seed 0x%016llx\n", (unsigned long long)seed);

    for (int t = 0; t < 2; t++) pools[t].srv.sock = -1;
    for (int t = 0; t < 2; t++)
        if (pool_open(&pools[t], exes[t], cwds[t], shim) != 0) {
            cleanup();
            return fail("%s", os_error);
        }

    char err[2048] = "";
    engine_options options = {lib, wasm, cwasm, ENGINE_SHA256};
    engine *e = engine_open(&options, err, sizeof err);
    if (!e) {
        cleanup();
        return fail("%s", err);
    }
    progress.start = progress.round_start = now();
    engine_callbacks callbacks = {NULL, on_reply, on_spawn, on_death, on_notice};
    int32_t result[12];
    int code = engine_run(e, &callbacks, map.data, map.len, debug, seed, result, err, sizeof err);
    if (code == ENGINE_OK || code == ENGINE_FAILED) notices += notice_partial, notice_partial = 0;
    cleanup();
    if (code == ENGINE_ABORTED) return fail("%s", os_error);
    if (code == ENGINE_FAILED) summarise(); /* engine.py's RuntimeError; a trap is the toolkit's own crash */
    if (code != ENGINE_OK) return fail("%s: %s", map_arg, err);
    summarise();
    int rounds = result[0] + 1;
    char reason[160];
    verdict(result, reason, sizeof reason);
    char took[32];
    elapsed(now() - progress.start, took, sizeof took);
    if (result[1] == 0)
        printf("%sdraw after %d rounds (%s) (%s)%s\n", paint_on(GREEN), rounds, reason, took, paint_off());
    else if (result[1] == 1 || result[1] == 2)
        printf("%steam %c wins after %d rounds (%s) (%s)%s\n", paint_on(GREEN), result[1] == 1 ? 'A' : 'B', rounds,
               reason, took, paint_off());
    else
        return fail("the engine named winner %d", result[1]);

    const uint8_t *blob;
    size_t len;
    if (engine_replay(e, names[0], names[1], &blob, &len, err, sizeof err) != ENGINE_OK)
        return fail("%s: %s", map_arg, err);
    if (write_replay(replay, blob, len) != 0)
        return fail("cannot write the replay to %s: [Errno %d] %s: '%s'", replay, errno, strerror(errno), replay);
    printf("%swrote replay: %s%s\n", paint_on(GREEN), replay, paint_off());
    engine_close(e);
    if (getenv("BC_JUDGE_CPU")) { /* native_fidelity.py --speed: the judge's own CPU (engine and glue), the bots' */
        struct rusage self, children;
        getrusage(RUSAGE_SELF, &self);
        getrusage(RUSAGE_CHILDREN, &children);
        fflush(stdout);
        fprintf(stderr, "cpu self %.6f children %.6f turns %ld\n",
                self.ru_utime.tv_sec + self.ru_stime.tv_sec + (self.ru_utime.tv_usec + self.ru_stime.tv_usec) / 1e6,
                children.ru_utime.tv_sec + children.ru_stime.tv_sec +
                    (children.ru_utime.tv_usec + children.ru_stime.tv_usec) / 1e6,
                turns);
    }
    return 0;
}
