#!/usr/bin/python3
"""Remote++ — a native setup and control panel for Chrome Remote Desktop."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from core import (APP_ID, BASE, GOOGLE_ACCESS, GOOGLE_SETUP, VERSION,
                  Backend, RemoteError, Status, parse_google_command, validate_pin)


def box(vertical=False, spacing=0, css=''):
    w = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, spacing=spacing)
    for name in css.split():
        w.add_css_class(name)
    return w


def label(text='', css='', wrap=False):
    w = Gtk.Label(label=text, xalign=0, wrap=wrap)
    w.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for name in css.split():
        w.add_css_class(name)
    return w


def button(text='', icon='', callback=None, css=''):
    w = Gtk.Button()
    if text and icon:
        child = box(spacing=8)
        child.set_halign(Gtk.Align.CENTER)
        child.append(Gtk.Image.new_from_icon_name(icon))
        child.append(label(text))
        w.set_child(child)
    elif icon:
        w.set_icon_name(icon)
    else:
        w.set_label(text)
    if callback:
        w.connect('clicked', callback)
    for name in css.split():
        w.add_css_class(name)
    return w


def card(spacing=14):
    return box(True, spacing, 'panel')


class Window(Adw.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title='Remote++')
        self.backend = application.backend
        self.preview = application.preview
        self.state = Status()
        self.busy = False
        self.refreshing = False
        self.updating = False
        self.set_default_size(1080, 800)
        self.set_size_request(480, 480)
        self.add_css_class('remote-window')
        self.connect('close-request', self.on_close)
        self.overlay = Adw.ToastOverlay()
        self.set_content(self.overlay)
        self.split = Adw.OverlaySplitView()
        self.split.set_min_sidebar_width(220)
        self.split.set_max_sidebar_width(220)
        self.overlay.set_child(self.split)
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 790px'))
        breakpoint.add_setter(self.split, 'collapsed', True)
        self.add_breakpoint(breakpoint)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.build_sidebar()
        self.build_main()
        self.build_overview()
        self.build_setup()
        self.build_security()
        self.build_diagnostics()
        self.select_page('overview')
        self.refresh()
        GLib.timeout_add_seconds(8, self.poll)

    def build_sidebar(self):
        toolbar = Adw.ToolbarView()
        toolbar.add_css_class('sidebar')
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_title_widget(Gtk.Box())
        toolbar.add_top_bar(header)
        content = box(True)
        brand = box(spacing=0)
        brand.set_margin_start(24)
        brand.set_margin_top(16)
        brand.set_margin_bottom(38)
        brand.append(label('Remote', 'brand'))
        brand.append(label('++', 'brand-plus'))
        content.append(brand)
        subtitle = label('УПРАВЛЕНИЕ', 'eyebrow')
        subtitle.set_margin_start(25)
        subtitle.set_margin_bottom(10)
        content.append(subtitle)
        self.nav = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.nav.add_css_class('nav-list')
        self.nav_rows = {}
        for key, title, icon in [('overview', 'Удалённый доступ', 'video-display-symbolic'),
                                 ('setup', 'Настройка', 'preferences-system-symbolic'),
                                 ('security', 'Безопасность', 'changes-prevent-symbolic'),
                                 ('diagnostics', 'Диагностика', 'dialog-information-symbolic')]:
            row = Gtk.ListBoxRow()
            row.page = key
            child = box(spacing=11)
            child.append(Gtk.Image.new_from_icon_name(icon))
            child.append(label(title))
            row.set_child(child)
            self.nav.append(row)
            self.nav_rows[key] = row
        self.nav.connect('row-selected', lambda _, row: self.select_page(row.page) if row else None)
        content.append(self.nav)
        content.append(Gtk.Box(vexpand=True))
        foot = box(True, 9, 'sidebar-footer')
        self.side_status = label('Проверка состояния…', 'tiny muted')
        foot.append(self.side_status)
        foot.append(label('Remote++  ' + VERSION, 'tiny muted'))
        foot.append(button('О приложении', 'help-about-symbolic', lambda _: self.about(), 'flat'))
        content.append(foot)
        toolbar.set_content(content)
        self.split.set_sidebar(toolbar)

    def build_main(self):
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_title_widget(Gtk.Box())
        toggle = button(icon='sidebar-show-symbolic', callback=lambda _: self.split.set_show_sidebar(True), css='flat')
        toggle.set_tooltip_text('Открыть навигацию')
        self.split.bind_property('collapsed', toggle, 'visible', 2)
        header.pack_start(toggle)
        refresh = button(icon='view-refresh-symbolic', callback=lambda _: self.refresh(), css='flat')
        refresh.set_tooltip_text('Обновить состояние')
        header.pack_end(refresh)
        toolbar.add_top_bar(header)
        outer = box(True)
        self.banner = Adw.Banner(title='')
        outer.append(self.banner)
        self.work = box(spacing=10, css='work-strip')
        self.spinner = Gtk.Spinner(spinning=True)
        self.work.append(self.spinner)
        self.work_label = label('', 'tiny', True)
        self.work.append(self.work_label)
        self.work.set_visible(False)
        outer.append(self.work)
        outer.append(self.stack)
        toolbar.set_content(outer)
        self.split.set_content(toolbar)

    def page(self, key, title, subtitle):
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=940, tightening_threshold=650)
        scroll.set_child(clamp)
        content = box(True, 22, 'page-content')
        clamp.set_child(content)
        headings = box(True, 7)
        headings.append(label(title, 'page-title', True))
        headings.append(label(subtitle, 'muted', True))
        content.append(headings)
        self.stack.add_named(scroll, key)
        return content

    def build_overview(self):
        content = self.page('overview', 'Удалённый доступ', 'Chrome Remote Desktop · GNOME / Wayland')
        hero = card(20)
        top = box(spacing=18)
        image = Gtk.Image.new_from_file(str(BASE / 'data' / (APP_ID + '.svg')))
        image.set_pixel_size(82)
        top.append(image)
        headings = box(True, 7)
        headings.set_hexpand(True)
        headings.set_valign(Gtk.Align.CENTER)
        self.status_pill = label('ПРОВЕРКА СОСТОЯНИЯ', 'status-pill')
        headings.append(self.status_pill)
        self.hero_title = label('Проверка состояния…', 'hero-title', True)
        headings.append(self.hero_title)
        self.hero_subtitle = label('Получение состояния службы.', 'muted', True)
        headings.append(self.hero_subtitle)
        top.append(headings)
        hero.append(top)
        self.primary = button('Настроить доступ', 'list-add-symbolic', self.primary_action, 'suggested-action large-button')
        self.primary.set_halign(Gtk.Align.START)
        hero.append(self.primary)
        content.append(hero)
        details = Adw.PreferencesGroup()
        self.device_row = Adw.ActionRow(title='Этот компьютер', subtitle='—')
        self.device_row.add_prefix(Gtk.Image.new_from_icon_name('computer-symbolic'))
        self.device_row.add_css_class('property-row')
        details.add(self.device_row)
        self.account_row = Adw.ActionRow(title='Аккаунт Google', subtitle='Не привязан')
        self.account_row.add_prefix(Gtk.Image.new_from_icon_name('avatar-default-symbolic'))
        details.add(self.account_row)
        content.append(details)
        metrics = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=1, max_children_per_line=3, column_spacing=12, row_spacing=12)
        self.metrics = {}
        for key, title, icon in [('screen', 'Рабочий стол', 'video-display-symbolic'), ('memory', 'Память службы', 'org.gnome.Settings-device-security-symbolic'), ('autostart', 'Автозапуск', 'system-run-symbolic')]:
            tile = box(True, 7, 'metric')
            tile.append(label(title, 'tiny muted'))
            value = label('—', 'metric-value', True)
            tile.append(value)
            self.metrics[key] = value
            metrics.insert(tile, -1)
        content.append(metrics)
        connect = card(12)
        connect.append(label('Как подключиться', 'section-title'))
        connect.append(label('Откройте Chrome Remote Desktop под тем же аккаунтом Google, выберите компьютер и введите PIN.', 'muted', True))
        open_button = button('Открыть Chrome Remote Desktop', 'web-browser-symbolic', lambda _: self.open_url(GOOGLE_ACCESS))
        open_button.set_halign(Gtk.Align.START)
        connect.append(open_button)
        content.append(connect)
        content.append(label('Доступ работает в запущенном сеансе GNOME. Захват экрана включается при подключении клиента.', 'tiny muted', True))

    def build_setup(self):
        content = self.page('setup', 'Настройка', 'Установка компонента Google и регистрация компьютера.')
        group = Adw.PreferencesGroup(title='1. Компонент Google')
        self.host_row = Adw.ActionRow(title='Chrome Remote Desktop', subtitle='Проверка установки…')
        self.host_row.add_prefix(Gtk.Image.new_from_icon_name('package-x-generic-symbolic'))
        self.install_button = button('Установить', callback=lambda _: self.install_host(), css='suggested-action')
        self.install_button.set_valign(Gtk.Align.CENTER)
        self.host_row.add_suffix(self.install_button)
        group.add(self.host_row)
        content.append(group)
        self.already = card()
        self.already.append(label('Этот компьютер уже привязан', 'section-title'))
        self.already_text = label('', 'muted', True)
        self.already.append(self.already_text)
        self.already.append(button('Привязать другой аккаунт…', callback=lambda _: self.confirm_disconnect(), css='destructive-action'))
        content.append(self.already)
        self.setup_form = box(True, 22)
        google = card()
        google.append(label('2. Авторизация Google', 'section-title'))
        google.append(label('На странице Google выберите «Начать», затем «Авторизовать». Скопируйте команду для Debian Linux и вставьте ниже.', 'muted', True))
        open_button = button('Открыть авторизацию Google', 'web-browser-symbolic', lambda _: self.open_url(GOOGLE_SETUP))
        open_button.set_halign(Gtk.Align.START)
        google.append(open_button)
        group = Adw.PreferencesGroup()
        self.command = Adw.PasswordEntryRow(title='Команда Debian Linux от Google')
        group.add(self.command)
        google.append(group)
        google.append(label('Код авторизации действует ограниченное время.', 'tiny muted', True))
        self.setup_form.append(google)
        identity = Adw.PreferencesGroup(title='3. Имя компьютера и PIN', description='Имя латиницей. PIN для подключения: от 6 до 20 цифр.')
        self.device_name = Adw.EntryRow(title='Имя компьютера')
        self.device_name.set_text('CachyOS PC')
        identity.add(self.device_name)
        self.setup_pin = Adw.PasswordEntryRow(title='PIN для подключения')
        self.setup_confirm = Adw.PasswordEntryRow(title='Повторите PIN')
        for entry in (self.setup_pin, self.setup_confirm):
            entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
            identity.add(entry)
        self.setup_form.append(identity)
        self.connect_button = button('Включить удалённый доступ', 'network-transmit-receive-symbolic', lambda _: self.connect_google(), 'suggested-action large-button')
        self.connect_button.set_halign(Gtk.Align.START)
        self.setup_form.append(self.connect_button)
        content.append(self.setup_form)

    def build_security(self):
        content = self.page('security', 'Безопасность', 'PIN, автозапуск и отключение доступа.')
        group = Adw.PreferencesGroup(title='Подключение')
        self.autostart = Adw.SwitchRow(title='Автозапуск при входе', subtitle='Включать удалённый доступ после входа в GNOME')
        self.autostart.connect('notify::active', self.toggle_autostart)
        group.add(self.autostart)
        content.append(group)
        pin = Adw.PreferencesGroup(title='Новый PIN', description='Смена PIN перезапустит службу и прервёт текущее удалённое подключение.')
        self.new_pin = Adw.PasswordEntryRow(title='Новый PIN')
        self.new_confirm = Adw.PasswordEntryRow(title='Повторите новый PIN')
        for row in (self.new_pin, self.new_confirm):
            row.set_input_purpose(Gtk.InputPurpose.DIGITS)
            pin.add(row)
        content.append(pin)
        self.pin_button = button('Сохранить PIN', 'changes-prevent-symbolic', lambda _: self.change_pin(), 'suggested-action')
        self.pin_button.set_halign(Gtk.Align.START)
        content.append(self.pin_button)
        control = card()
        control.append(label('Отключить доступ', 'section-title'))
        control.append(label('Служба будет остановлена, автозапуск выключен. Регистрация Google сохранится.', 'muted', True))
        stop = button('Выключить удалённый доступ', callback=lambda _: self.stop_access(), css='destructive-action')
        stop.set_halign(Gtk.Align.START)
        control.append(stop)
        content.append(control)

    def build_diagnostics(self):
        content = self.page('diagnostics', 'Диагностика', 'Состояние службы и журнал событий.')
        group = Adw.PreferencesGroup()
        self.compat_row = Adw.ActionRow(title='Графический сеанс', subtitle='—')
        self.server_row = Adw.ActionRow(title='Компонент Google', subtitle='—')
        self.service_row = Adw.ActionRow(title='Служба удалённого доступа', subtitle='—')
        for row in (self.compat_row, self.server_row, self.service_row):
            group.add(row)
        content.append(group)
        actions = box(spacing=10)
        actions.append(button('Обновить журнал', 'view-refresh-symbolic', lambda _: self.load_diagnostics()))
        actions.append(button('Копировать', 'edit-copy-symbolic', lambda _: self.copy_diagnostics()))
        content.append(actions)
        self.log = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.log.add_css_class('log-view')
        self.log.get_buffer().set_text('Нажмите «Обновить журнал» для загрузки событий.')
        scroll = Gtk.ScrolledWindow(min_content_height=250, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add_css_class('log-panel')
        scroll.set_child(self.log)
        content.append(scroll)
        content.append(label('В журнале скрываются адрес аккаунта и коды авторизации.', 'tiny muted', True))

    def select_page(self, key):
        self.stack.set_visible_child_name(key)
        if self.nav.get_selected_row() != self.nav_rows[key]:
            self.nav.select_row(self.nav_rows[key])
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)

    def open_url(self, url):
        launcher = Gtk.UriLauncher.new(url)
        launcher.launch(self, None, self.url_finished)

    def url_finished(self, launcher, result):
        try:
            launcher.launch_finish(result)
        except GLib.Error:
            self.error('Не удалось открыть браузер', 'Откройте адрес вручную: ' + launcher.get_uri())

    def error(self, title, body):
        dialog = Adw.AlertDialog(heading=title, body=body)
        dialog.add_response('ok', 'Закрыть')
        dialog.set_default_response('ok')
        dialog.present(self)

    def toast(self, text):
        self.overlay.add_toast(Adw.Toast.new(text))

    def task(self, message, operation, success='Готово', after=None):
        if self.busy:
            return
        if self.preview:
            self.toast('Режим предпросмотра: настройки компьютера не меняются')
            return
        self.busy = True
        self.work_label.set_text(message)
        self.work.set_visible(True)
        self.stack.set_sensitive(False)
        def worker():
            try:
                result = operation()
                GLib.idle_add(finish, None, result)
            except Exception as exc:
                message = str(exc) if isinstance(exc, RemoteError) else 'Операция не выполнена. Проверьте журнал и повторите попытку.'
                GLib.idle_add(finish, message, None)
        def finish(error, result):
            self.busy = False
            self.work.set_visible(False)
            self.stack.set_sensitive(True)
            if error:
                self.error('Операция не выполнена', error)
            else:
                if success:
                    self.toast(success)
                if after:
                    after(result)
            self.refresh()
            return False
        threading.Thread(target=worker, daemon=True).start()

    def report(self, text):
        GLib.idle_add(self.work_label.set_text, text)

    def refresh(self):
        if self.busy or self.refreshing:
            return
        if self.preview:
            mode = os.environ.get('REMOTEPLUS_PREVIEW_STATE', 'ready')
            self.update_state(Status(compatible=True, reason='CachyOS / Arch · GNOME · Wayland',
                host_installed=mode != 'new', host_version='157.0.8103.19' if mode != 'new' else '',
                registered=mode != 'new', active=mode == 'ready', online=mode == 'ready',
                enabled=mode == 'ready', name='CachyOS PC', account='my.account@gmail.com' if mode != 'new' else '', memory='59 МБ' if mode == 'ready' else '—'))
            return
        self.refreshing = True
        def worker():
            try:
                state = self.backend.status()
                GLib.idle_add(finish, state, None)
            except RemoteError as exc:
                GLib.idle_add(finish, None, str(exc))
            except Exception:
                GLib.idle_add(finish, None, 'Не удалось обновить состояние. Откройте диагностику.')
        def finish(state, error):
            self.refreshing = False
            if error:
                self.banner.set_title(error)
                self.banner.set_revealed(True)
            else:
                self.update_state(state)
            return False
        threading.Thread(target=worker, daemon=True).start()

    def update_state(self, s):
        self.state = s
        self.updating = True
        self.banner.set_revealed(not s.compatible or bool(s.problem) or s.legacy_active)
        self.banner.set_title(s.reason if not s.compatible else 'Обнаружена служба отдельного рабочего стола. Откройте диагностику.' if s.legacy_active else 'Соединение с Google не установлено. Проверьте подключение к интернету и журнал.' if s.problem else '')
        for css in ('online', 'offline'):
            self.status_pill.remove_css_class(css)
        if s.online:
            pill, title, subtitle, action = 'В СЕТИ', 'Доступ включён', 'Соединение с Google установлено.', 'Выключить доступ'
            self.status_pill.add_css_class('online')
        elif s.active:
            pill, title, subtitle, action = 'ПОДКЛЮЧЕНИЕ', 'Соединение с Google…', 'Если соединение не устанавливается, проверьте журнал в разделе «Диагностика».', 'Выключить доступ'
        elif s.registered:
            pill, title, subtitle, action = 'ОТКЛЮЧЁН', 'Доступ выключен', 'Регистрация Google сохранена.', 'Включить доступ'
            self.status_pill.add_css_class('offline')
        else:
            pill, title, subtitle, action = 'ТРЕБУЕТСЯ НАСТРОЙКА', 'Доступ не настроен', 'Для подключения необходимы аккаунт Google и PIN.', 'Настроить доступ'
        self.status_pill.set_text(pill)
        self.hero_title.set_text(title)
        self.hero_subtitle.set_text(subtitle)
        self.primary.set_child(label(action))
        self.primary.set_sensitive(s.compatible)
        self.side_status.set_text('●  ' + ('Доступен в сети' if s.online else 'Подключение…' if s.active else 'Доступ выключен'))
        self.device_row.set_subtitle(s.name or '—')
        self.account_row.set_subtitle(s.account or 'Не привязан')
        self.metrics['screen'].set_text('Текущий')
        self.metrics['memory'].set_text(s.memory)
        self.metrics['autostart'].set_text('Включён' if s.enabled else 'Выключен')
        self.host_row.set_subtitle(('Установлен · ' + s.host_version) if s.host_installed else 'Не установлен')
        self.install_button.set_visible(not s.host_installed)
        self.install_button.set_sensitive(s.compatible)
        self.setup_form.set_visible(not s.registered)
        self.already.set_visible(s.registered)
        self.already_text.set_text(f'{s.name}\n{s.account}')
        self.connect_button.set_sensitive(s.host_installed and s.compatible)
        self.autostart.set_active(s.enabled)
        self.autostart.set_sensitive(s.registered and s.compatible)
        self.pin_button.set_sensitive(s.registered)
        self.compat_row.set_subtitle(s.reason)
        self.server_row.set_subtitle(s.host_version or 'Не установлен')
        self.service_row.set_subtitle('Доступен в сети' if s.online else 'Соединяется с Google' if s.active else 'Остановлена')
        self.updating = False

    def primary_action(self, _):
        if not self.state.registered:
            self.select_page('setup')
        elif self.state.active:
            self.stop_access()
        else:
            self.task('Включение удалённого доступа…', lambda: self.backend.start(True), 'Доступ включён')

    def install_host(self):
        self.task('Подготовка установки…', lambda: self.backend.install_host(self.report), 'Компонент Google установлен')

    def connect_google(self):
        command, name = self.command.get_text(), self.device_name.get_text()
        pin, confirmation = self.setup_pin.get_text(), self.setup_confirm.get_text()
        try:
            validate_pin(pin, confirmation)
            parse_google_command(command)
        except RemoteError as exc:
            self.error('Некорректные данные', str(exc))
            return
        def done(_):
            for entry in (self.command, self.setup_pin, self.setup_confirm):
                entry.set_text('')
            self.select_page('overview')
        self.task('Регистрация компьютера в Google…', lambda: self.backend.connect_google(command, name, pin, confirmation), 'Компьютер привязан', done)

    def change_pin(self):
        pin, confirmation = self.new_pin.get_text(), self.new_confirm.get_text()
        try:
            validate_pin(pin, confirmation)
        except RemoteError as exc:
            self.error('Некорректный PIN', str(exc))
            return
        def done(_):
            self.new_pin.set_text('')
            self.new_confirm.set_text('')
        self.task('Сохранение PIN…', lambda: self.backend.change_pin(pin, confirmation), 'Новый PIN сохранён', done)

    def toggle_autostart(self, row, _):
        if not self.updating:
            value = row.get_active()
            self.task('Изменение автозапуска…', lambda: self.backend.set_autostart(value), 'Автозапуск включён' if value else 'Автозапуск выключен')

    def stop_access(self):
        def stop():
            self.backend.stop()
            self.backend.set_autostart(False)
        self.task('Отключение удалённого доступа…', stop, 'Удалённый доступ выключен')

    def confirm_disconnect(self):
        dialog = Adw.AlertDialog(heading='Отвязать этот компьютер?', body='Удалённый доступ будет выключен. После новой привязки этот компьютер появится в другом аккаунте. Старую запись можно удалить на странице Google.')
        dialog.add_response('cancel', 'Отмена')
        dialog.add_response('disconnect', 'Отвязать')
        dialog.set_response_appearance('disconnect', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', lambda _, response: self.task('Удаление привязки…', self.backend.disconnect, 'Компьютер отвязан') if response == 'disconnect' else None)
        dialog.present(self)

    def load_diagnostics(self):
        if self.preview:
            self.log.get_buffer().set_text(f'Remote++ {VERSION}\nСовместимость: CachyOS / Arch · GNOME · Wayland\nКомпонент Google: 157.0.8103.19\n\nRemote++: waiting for an authenticated client; capture is not started.\nSignaling connected.\nHost ready to receive connections.')
            return
        self.task('Загрузка журнала…', self.backend.diagnostics, '', lambda text: self.log.get_buffer().set_text(text))

    def copy_diagnostics(self):
        buffer = self.log.get_buffer()
        self.get_clipboard().set(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False))
        self.toast('Диагностика скопирована')

    def poll(self):
        if self.get_visible():
            self.refresh()
        return True

    def on_close(self, _):
        if self.busy:
            self.toast('Дождитесь завершения операции')
            return True
        return False

    def about(self):
        dialog = Adw.AboutDialog(application_name='Remote++', application_icon=APP_ID,
            developer_name='edgarsskills-fix', version=VERSION, license_type=Gtk.License.MIT_X11,
            comments='Настройка Chrome Remote Desktop для GNOME на Wayland.',
            website='https://github.com/edgarsskills-fix/remote-plus-plus')
        dialog.set_copyright('© 2026 edgarsskills-fix')
        dialog.present(self)


class App(Adw.Application):
    def __init__(self, preview=False):
        super().__init__(application_id=APP_ID + ('.Preview' if preview else ''), flags=Gio.ApplicationFlags.NON_UNIQUE if preview else Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.preview = preview
        self.backend = Backend()
        self.window = None

    def do_activate(self):
        if self.window is None:
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            theme.add_search_path(str(BASE / 'data'))
            provider = Gtk.CssProvider()
            provider.load_from_path(str(BASE / 'data/style.css'))
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            scheme = os.environ.get('REMOTEPLUS_THEME')
            if scheme in ('dark', 'light'):
                self.get_style_manager().set_color_scheme(Adw.ColorScheme.FORCE_DARK if scheme == 'dark' else Adw.ColorScheme.FORCE_LIGHT)
            self.window = Window(self)
        self.window.present()


if __name__ == '__main__':
    app = App(preview='--preview' in sys.argv)
    raise SystemExit(app.run([sys.argv[0]]))
