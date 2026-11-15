/* Remote++: defer Mutter capture until CRD reports an authenticated client.
 * Loaded only into the pinned Google host, never into GNOME Shell.
 * SPDX-License-Identifier: MIT
 */
#define _GNU_SOURCE
#include <gio/gio.h>
#include <dlfcn.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <syslog.h>
#include <unistd.h>

typedef void (*Call)(GDBusConnection *, const gchar *, const gchar *,
                     const gchar *, const gchar *, GVariant *,
                     const GVariantType *, GDBusCallFlags, gint,
                     GCancellable *, GAsyncReadyCallback, gpointer);
typedef void (*VSyslog)(int, const char *, va_list);
typedef void (*CallFds)(GDBusConnection *, const gchar *, const gchar *,
                     const gchar *, const gchar *, GVariant *,
                     const GVariantType *, GDBusCallFlags, gint,
                     GUnixFDList *, GCancellable *, GAsyncReadyCallback, gpointer);

static Call real_call;
static CallFds real_call_fds;
static VSyslog real_vsyslog;
static int event_fd = -1;
static GMutex clients_lock;
static GHashTable *clients;
static atomic_int active_clients;

static void notify(const char *event) {
    if (send(event_fd, event, strlen(event), MSG_NOSIGNAL) < 0)
        _exit(78); /* No controller: never continue with an orphaned capture. */
}

__attribute__((constructor)) static void init(void) {
    if (prctl(PR_SET_PDEATHSIG, SIGTERM) || getppid() == 1)
        _exit(78);
    const char *value = getenv("REMOTEPLUS_GATE_FD");
    char *end = NULL;
    long fd = value ? strtol(value, &end, 10) : -1;
    if (!value || !*value || *end || fd < 3 || fd > 1048576)
        _exit(78);
    event_fd = (int)fd;
    real_call = (Call)dlsym(RTLD_NEXT, "g_dbus_connection_call");
    real_call_fds = (CallFds)dlsym(RTLD_NEXT, "g_dbus_connection_call_with_unix_fd_list");
    real_vsyslog = (VSyslog)dlsym(RTLD_NEXT, "vsyslog");
    if (!real_call || !real_call_fds || !real_vsyslog)
        _exit(78);
    clients = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, NULL);
    notify("ready");
}

/* Google's HostEventLoggerPosix emits these only after authentication, and
 * once on authenticated-session teardown. Initial connection attempts and
 * incorrect PINs do not open the gate. Keep forwarding the original syslog.
 */
void syslog(int priority, const char *format, ...) {
    char message[4096];
    va_list args, copy;
    va_start(args, format);
    va_copy(copy, args);
    int size = vsnprintf(message, sizeof(message), format, copy);
    va_end(copy);
    real_vsyslog(priority, format, args);
    va_end(args);
    if ((priority & LOG_PRIMASK) != LOG_NOTICE || strcmp(format, "%s") ||
        size < 0 || (size_t)size >= sizeof(message))
        return;
    const char *connected = "Client connected: ";
    const char *disconnected = "Client disconnected: ";
    g_mutex_lock(&clients_lock);
    if (g_str_has_prefix(message, connected) && message[strlen(connected)]) {
        g_hash_table_add(clients, g_strdup(message + strlen(connected)));
        atomic_store(&active_clients, (int)g_hash_table_size(clients));
        notify("active");
    } else if (g_str_has_prefix(message, disconnected) &&
               g_hash_table_remove(clients, message + strlen(disconnected))) {
        atomic_store(&active_clients, (int)g_hash_table_size(clients));
        if (atomic_load(&active_clients) == 0)
            notify("idle");
    }
    g_mutex_unlock(&clients_lock);
}

typedef struct {
    GDBusConnection *connection;
    gchar *bus, *path, *interface, *method;
    GVariant *parameters;
    GVariantType *reply_type;
    GDBusCallFlags flags;
    gint timeout;
    GCancellable *cancellable;
    GAsyncReadyCallback callback;
    gpointer data;
    GMainContext *context;
    GUnixFDList *fds;
    gboolean with_fds;
} Pending;

static gboolean try_call(gpointer opaque) {
    Pending *p = opaque;
    if (!atomic_load(&active_clients) &&
        !(p->cancellable && g_cancellable_is_cancelled(p->cancellable)))
        return G_SOURCE_CONTINUE;
    /* Preserve the original GIO callback context, not the supervisor's. */
    g_main_context_push_thread_default(p->context);
    if (p->with_fds) {
        real_call_fds(p->connection, p->bus, p->path, p->interface, p->method,
              p->parameters, p->reply_type, p->flags, p->timeout, p->fds,
              p->cancellable, p->callback, p->data);
    } else {
        real_call(p->connection, p->bus, p->path, p->interface, p->method,
              p->parameters, p->reply_type, p->flags, p->timeout,
              p->cancellable, p->callback, p->data);
    }
    g_main_context_pop_thread_default(p->context);
    notify("released");
    return G_SOURCE_REMOVE;
}

static void free_pending(gpointer opaque) {
    Pending *p = opaque;
    g_object_unref(p->connection);
    g_free(p->bus); g_free(p->path); g_free(p->interface); g_free(p->method);
    if (p->parameters) g_variant_unref(p->parameters);
    if (p->reply_type) g_variant_type_free(p->reply_type);
    g_clear_object(&p->cancellable);
    g_clear_object(&p->fds);
    g_main_context_unref(p->context);
    g_free(p);
}

static gboolean should_hold(const gchar *bus, const gchar *path,
                            const gchar *interface, const gchar *method) {
    return !g_strcmp0(bus, "org.gnome.Mutter.RemoteDesktop") &&
        !g_strcmp0(path, "/org/gnome/Mutter/RemoteDesktop") &&
        !g_strcmp0(interface, "org.gnome.Mutter.RemoteDesktop") &&
        !g_strcmp0(method, "CreateSession") && !atomic_load(&active_clients);
}

static void hold(GDBusConnection *connection, const gchar *bus,
    const gchar *path, const gchar *interface, const gchar *method,
    GVariant *parameters, const GVariantType *reply_type, GDBusCallFlags flags,
    gint timeout, GCancellable *cancellable, GAsyncReadyCallback callback,
    gpointer data, gboolean with_fds, GUnixFDList *fds) {
    Pending *p = g_new0(Pending, 1);
    p->connection = g_object_ref(connection);
    p->bus = g_strdup(bus); p->path = g_strdup(path);
    p->interface = g_strdup(interface); p->method = g_strdup(method);
    /* Like GIO, consume a floating variant and retain non-floating ones. */
    p->parameters = parameters ? g_variant_ref_sink(parameters) : NULL;
    p->reply_type = reply_type ? g_variant_type_copy(reply_type) : NULL;
    p->flags = flags; p->timeout = timeout;
    p->cancellable = cancellable ? g_object_ref(cancellable) : NULL;
    p->callback = callback; p->data = data;
    p->with_fds = with_fds;
    p->fds = fds ? g_object_ref(fds) : NULL;
    p->context = g_main_context_ref_thread_default();
    GSource *source = g_timeout_source_new(50);
    g_source_set_callback(source, try_call, p, free_pending);
    g_source_attach(source, p->context);
    g_source_unref(source);
    notify("held");
}

void g_dbus_connection_call(GDBusConnection *connection, const gchar *bus,
    const gchar *path, const gchar *interface, const gchar *method,
    GVariant *parameters, const GVariantType *reply_type, GDBusCallFlags flags,
    gint timeout, GCancellable *cancellable, GAsyncReadyCallback callback,
    gpointer data) {
    if (should_hold(bus, path, interface, method)) {
        hold(connection, bus, path, interface, method, parameters, reply_type,
             flags, timeout, cancellable, callback, data, FALSE, NULL);
    } else {
        real_call(connection, bus, path, interface, method, parameters,
                  reply_type, flags, timeout, cancellable, callback, data);
    }
}

void g_dbus_connection_call_with_unix_fd_list(GDBusConnection *connection,
    const gchar *bus, const gchar *path, const gchar *interface, const gchar *method,
    GVariant *parameters, const GVariantType *reply_type, GDBusCallFlags flags,
    gint timeout, GUnixFDList *fds, GCancellable *cancellable,
    GAsyncReadyCallback callback, gpointer data) {
    if (should_hold(bus, path, interface, method)) {
        hold(connection, bus, path, interface, method, parameters, reply_type,
             flags, timeout, cancellable, callback, data, TRUE, fds);
    } else {
        real_call_fds(connection, bus, path, interface, method, parameters,
                  reply_type, flags, timeout, fds, cancellable, callback, data);
    }
}
