
# Copyright (c) 2009 - Luca Invernizzi <invernizzi.l@gmail.com>
#               2012 - Izidor Matušov <izidor.matusov@gmail.com>
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program.  If not, see <http://www.gnu.org/licenses/>.

""" Export plugin
Plugin for exporting into nice lists in TXT, HTML or PDF """

import os
import shutil
import subprocess
import webbrowser
import logging

from gi.repository import GObject, Gtk, GdkPixbuf, GLib

from gettext import gettext as _
from GTG.plugins.export.task_str import get_task_wrappers
from GTG.plugins.export.templates import Template, get_templates_paths

log = logging.getLogger(__name__)


# Enforce external dependencies
for dependence in "pdflatex", "pdftk", "pdfjam":
    retval = subprocess.call(["which", dependence],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL,)
    if retval != 0:
        log.debug('Missing command %r', dependence)
        raise ImportError(f'Missing command "{dependence}"')


def get_desktop_dir():
    """ Returns path to desktop dir. """
    return GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP)


class ExportPlugin():
    """ Export plugin - handle UI and trigger exporting tasks """

    # Allow initilization outside __init__() and don't complain
    # about too many attributes

    PLUGIN_NAME = "export"

    DEFAULT_PREFERENCES = {
        "last_template": None,
    }

    def __init__(self):
        self.filename = None
        self.template = None

    def activate(self, plugin_api):
        """ Loads saved preferences """
        self.plugin_api = plugin_api
        self._init_gtk()
        self._preferences_load()
        self._preferences_apply()

    def deactivate(self, plugin_api):
        """ Removes the gtk widgets before quitting """
        self._gtk_deactivate()

# CALLBACK AND CORE FUNCTIONS #################################################
    def on_export_start(self, saving):
        """ Start generating a document.
        If saving == True, ask user where to store the document. Otherwise,
        open it afterwards. """

        model = self.combo.get_model()
        active = self.combo.get_active()
        self.template = Template(model[active][0])

        tasks = self.get_selected_tasks()
        if len(tasks) == 0:
            self.show_error_dialog(_("No task matches your criteria. "
                                     "Empty report can't be generated."))
            return

        self.filename = None
        if saving:
            self.filename = self.choose_file()
            if self.filename is None:
                return

        self.save_button.set_sensitive(False)
        self.open_button.set_sensitive(False)

        try:
            self.template.generate(tasks, self.plugin_api,
                                   self.on_export_finished)
        except Exception as err:
            self.show_error_dialog(
                _("GTG could not generate the document: %s") % err)
            raise

    def on_export_finished(self):
        """ Save generated file or open it, reenable buttons
        and hide dialog """
        document_path = self.template.get_document_path()
        if document_path:
            if self.filename:
                shutil.copyfile(document_path, self.filename)
            else:
                webbrowser.open(document_path)
        else:
            self.show_error_dialog("Document creation failed. "
                                   "Ensure you have all needed programs.")

        self.save_button.set_sensitive(True)
        self.open_button.set_sensitive(True)
        self.export_dialog.hide()

    def get_selected_tasks(self):
        """ Filter tasks based on user option """
        timespan = None
        req = self.plugin_api.get_requester()

        if self.export_all_active.get_active():
            treename = 'active'
        elif self.export_all_finished.get_active():
            treename = 'closed'
        elif self.export_finished_last_week.get_active():
            treename = 'closed'
            timespan = -7

        tree = req.get_tasks_tree(name=treename)
        if treename not in tree.list_applied_filters():
            tree.apply_filter(treename)

        return get_task_wrappers(tree, timespan)

# GTK FUNCTIONS ###############################################################
    def _init_gtk(self):
        """ Initialize all the GTK widgets """
        self.menu_item = Gtk.ModelButton()
        self.menu_item.props.text = _("Export the tasks currently listed")
        self.menu_item.connect('clicked', self.show_dialog)
        self.menu_item.show()
        self.plugin_api.add_menu_item(self.menu_item)

        builder = Gtk.Builder()
        cur_dir = os.path.dirname(os.path.abspath(__file__))
        builder_file = os.path.join(cur_dir, "export.ui")
        builder.add_from_file(builder_file)

        self.combo = builder.get_object("export_combo_templ")
        templates_list = Gtk.ListStore(
            GObject.TYPE_STRING, GObject.TYPE_STRING, GObject.TYPE_STRING,
            GObject.TYPE_STRING)
        self.combo.set_model(templates_list)
        cell = Gtk.CellRendererText()
        self.combo.pack_start(cell, True)
        self.combo.add_attribute(cell, 'text', 1)

        self.export_dialog = builder.get_object("export_dialog")
        self.export_image = builder.get_object("export_image")
        self.description_label = builder.get_object("label_description")
        self.save_button = builder.get_object("export_btn_save")
        self.open_button = builder.get_object("export_btn_open")

        self.export_all_active = builder.get_object(
            "export_all_active_rb")
        self.export_all_active.set_active(True)
        self.export_finished_last_week = builder.get_object(
            "export_finished_last_week_rb")
        self.export_all_finished = builder.get_object(
            "export_all_finished_rb")

        builder.connect_signals({
            "on_export_btn_open_clicked":
            lambda widget: self.on_export_start(False),
            "on_export_btn_save_clicked":
            lambda widget: self.on_export_start(True),
            "on_export_dialog_delete_event":
            self._hide_dialog,
            "on_export_combo_templ_changed":
            self.on_combo_changed,
        })

    def _gtk_deactivate(self):
        """ Remove Menu item for this plugin """
        self.plugin_api.remove_menu_item(self.menu_item)

    def show_dialog(self, widget):
        """ Show dialog with options for export """
        parent_window = self.plugin_api.get_ui().get_window()
        self.export_dialog.set_transient_for(parent_window)
        self._update_combobox()
        self.export_dialog.show_all()

    def _hide_dialog(self, sender=None, data=None):

        """ Hide dialog """
        self.export_dialog.hide()
        return True

    def _update_combobox(self):
        """ Reload list of templates """
        model = self.combo.get_model()
        model.clear()

        templates = get_templates_paths()
        active_entry = None
        for i, path in enumerate(templates):
            template = Template(path)
            if path == self.preferences["last_template"]:
                active_entry = i

            model.append((path,
                          template.get_title(),
                          template.get_description(),
                          template.get_image_path()))

        # wrap the combo-box if it's too long
        if len(templates) > 15:
            self.combo.set_wrap_width(5)

        if active_entry is None:
            active_entry = 0
        self.combo.set_active(active_entry)

    def on_combo_changed(self, combo):
        """ Display details about the selected template """
        model = combo.get_model()
        active = combo.get_active()
        if not 0 <= active < len(model):
            return
        description, image = model[active][2], model[active][3]

        if image:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(image)
            width, height = self.export_image.get_size_request()
            pixbuf = pixbuf.scale_simple(width, height,
                                         GdkPixbuf.InterpType.BILINEAR)
            self.export_image.set_from_pixbuf(pixbuf)
        else:
            self.export_image.clear()
        self.description_label.set_markup(f"<i>{description}</i>")

        # Remember the last selected path
        self.preferences["last_template"] = model[active][0]
        self._preferences_store()

    def show_error_dialog(self, message):
        """ Display an error """
        dialog = Gtk.MessageDialog(
            parent=self.export_dialog,
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
            type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            message_format=message)
        dialog.run()
        dialog.destroy()

    def choose_file(self):
        """ Let user choose a file to save and return its path """
        chooser = Gtk.FileChooserNative.new(
            _("Choose where to save your list"),
            self.export_dialog,
            Gtk.FileChooserAction.SAVE,
            None,
            None)
        chooser.set_do_overwrite_confirmation(True)
        chooser.set_current_folder(get_desktop_dir())
        response = chooser.run()
        filename = chooser.get_filename()
        chooser.destroy()
        if response == Gtk.ResponseType.ACCEPT:
            return filename
        else:
            return None

# Preferences methods #########################################################
    @classmethod
    def is_configurable(cls):
        """A configurable plugin should have this method and return True"""
        return False

    def configure_dialog(self, manager_dialog):
        """ Display configuration dialog """
        self._preferences_load()

    def _preferences_load(self):
        """ Restore user preferences """
        self.preferences = self.plugin_api.load_configuration_object(
            self.PLUGIN_NAME, "preferences",
            default_values=self.DEFAULT_PREFERENCES)

    def _preferences_store(self):
        """ Store user preferences """
        self.plugin_api.save_configuration_object(
            self.PLUGIN_NAME, "preferences", self.preferences)

    def _preferences_apply(self):
        """ Apply preferences """
        pass
