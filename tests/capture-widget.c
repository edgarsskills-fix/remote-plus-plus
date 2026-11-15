#include <gtk/gtk.h>

/* Snapshot our own test window; no access to other windows or the desktop. */
int capture_widget(GtkWidget *widget, const char *path) {
    int width = gtk_widget_get_width(widget), height = gtk_widget_get_height(widget);
    GtkNative *native = gtk_widget_get_native(widget);
    if (!native || width <= 0 || height <= 0) return 0;
    GdkPaintable *paintable = gtk_widget_paintable_new(widget);
    GtkSnapshot *snapshot = gtk_snapshot_new();
    gdk_paintable_snapshot(paintable, snapshot, width, height);
    GskRenderNode *node = gtk_snapshot_free_to_node(snapshot);
    g_object_unref(paintable);
    if (!node) return 0;
    graphene_rect_t bounds = GRAPHENE_RECT_INIT(0, 0, width, height);
    GdkTexture *texture = gsk_renderer_render_texture(gtk_native_get_renderer(native), node, &bounds);
    int result = gdk_texture_save_to_png(texture, path);
    g_object_unref(texture);
    gsk_render_node_unref(node);
    return result;
}
