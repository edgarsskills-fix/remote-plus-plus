/* Run on an isolated D-Bus: this never contacts the real desktop. */
#include <gio/gio.h>
#include <syslog.h>

static int starts, replies;
static GMainLoop *loop;

static gboolean finish_wait(gpointer ignored) {
    (void)ignored;
    g_main_loop_quit(loop);
    return G_SOURCE_REMOVE;
}

static void wait_ms(int ms, GMainContext *context) {
    GSource *timer = g_timeout_source_new(ms);
    g_source_set_callback(timer, finish_wait, NULL, NULL);
    g_source_attach(timer, context);
    g_source_unref(timer);
    g_main_loop_run(loop);
}

static void method(GDBusConnection *c, const gchar *sender, const gchar *path,
                   const gchar *interface, const gchar *name, GVariant *args,
                   GDBusMethodInvocation *invocation, gpointer data) {
    (void)c; (void)sender; (void)path; (void)interface; (void)args; (void)data;
    if (g_str_equal(name, "CreateSession")) {
        starts++;
        g_dbus_method_invocation_return_value(invocation, g_variant_new("(o)", "/test/session"));
    } else {
        g_dbus_method_invocation_return_value(invocation, NULL);
    }
}

static void reply(GObject *object, GAsyncResult *result, gpointer data) {
    GError *error = NULL;
    GVariant *value = data
        ? g_dbus_connection_call_with_unix_fd_list_finish(G_DBUS_CONNECTION(object), NULL, result, &error)
        : g_dbus_connection_call_finish(G_DBUS_CONNECTION(object), result, &error);
    g_assert_no_error(error);
    g_assert_nonnull(value);
    g_variant_unref(value);
    replies++;
}

int main(void) {
    GMainContext *context = g_main_context_new();
    g_main_context_push_thread_default(context);
    loop = g_main_loop_new(context, FALSE);
    GError *error = NULL;
    GDBusConnection *bus = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, &error);
    g_assert_no_error(error);
    const char *name = "org.gnome.Mutter.RemoteDesktop";
    const char *path = "/org/gnome/Mutter/RemoteDesktop";
    GVariant *ownership = g_dbus_connection_call_sync(bus, "org.freedesktop.DBus",
        "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
        g_variant_new("(su)", name, 0), G_VARIANT_TYPE("(u)"), 0, -1, NULL, &error);
    g_assert_no_error(error);
    g_variant_unref(ownership);
    GDBusNodeInfo *info = g_dbus_node_info_new_for_xml(
        "<node><interface name='org.gnome.Mutter.RemoteDesktop'>"
        "<method name='CreateSession'><arg type='o' direction='out'/></method>"
        "<method name='Ping'/></interface></node>", &error);
    g_assert_no_error(error);
    const GDBusInterfaceVTable vtable = {.method_call = method};
    guint object = g_dbus_connection_register_object(bus, path, info->interfaces[0], &vtable, NULL, NULL, &error);
    g_assert_no_error(error);
    g_dbus_connection_call(bus, name, path, name, "CreateSession", NULL,
                          G_VARIANT_TYPE("(o)"), 0, -1, NULL, reply, NULL);
    g_dbus_connection_call_with_unix_fd_list(bus, name, path, name, "CreateSession", NULL,
                          G_VARIANT_TYPE("(o)"), 0, -1, NULL, NULL, reply, GINT_TO_POINTER(1));
    g_dbus_connection_call(bus, name, path, name, "Ping", NULL, NULL, 0, -1, NULL, reply, NULL);
    wait_ms(200, context);
    g_assert_cmpint(starts, ==, 0);
    g_assert_cmpint(replies, ==, 1); /* unrelated calls still work */
    syslog(LOG_NOTICE, "%s", "Access denied for client: remote-plus-fixture-a");
    syslog(LOG_INFO, "%s", "Client connected: remote-plus-fixture-a");
    wait_ms(100, context);
    g_assert_cmpint(starts, ==, 0);
    syslog(LOG_NOTICE, "%s", "Client connected: remote-plus-fixture-a");
    wait_ms(200, context);
    g_assert_cmpint(starts, ==, 2);
    g_assert_cmpint(replies, ==, 3);
    /* Deduplication, simultaneous clients, and unknown disconnects. */
    syslog(LOG_NOTICE, "%s", "Client connected: remote-plus-fixture-a");
    syslog(LOG_NOTICE, "%s", "Client connected: remote-plus-fixture-b");
    syslog(LOG_NOTICE, "%s", "Client disconnected: unknown");
    syslog(LOG_NOTICE, "%s", "Client disconnected: remote-plus-fixture-a");
    wait_ms(100, context);
    syslog(LOG_NOTICE, "%s", "Client disconnected: remote-plus-fixture-b");
    g_dbus_connection_unregister_object(bus, object);
    g_dbus_node_info_unref(info);
    g_object_unref(bus);
    g_main_loop_unref(loop);
    g_main_context_pop_thread_default(context);
    g_main_context_unref(context);
    return 0;
}
