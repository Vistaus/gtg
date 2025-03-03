# -----------------------------------------------------------------------------
# Getting Things GNOME! - a personal organizer for the GNOME desktop
# Copyright (c) 2008-2013 - Lionel Dricot & Bertrand Rousseau
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
# -----------------------------------------------------------------------------

"""
task.py contains the Task class which represents (guess what) a task
"""
from datetime import datetime, date
import html
import re
import uuid
import logging
import xml.sax.saxutils as saxutils

from gi.repository import GLib

from gettext import gettext as _
from GTG.core.dates import Date
from liblarch import TreeNode

log = logging.getLogger(__name__)


class Task(TreeNode):
    """ This class represent a task in GTG.
    You should never create a Task directly. Use the datastore.new_task()
    function."""

    STA_ACTIVE = "Active"
    STA_DISMISSED = "Dismiss"
    STA_DONE = "Done"
    DEFAULT_TASK_NAME = None

    def __init__(self, task_id, requester, newtask=False):
        super().__init__(task_id)
        # the id of this task in the project should be set
        # tid is a string ! (we have to choose a type and stick to it)
        if not isinstance(task_id, str):
            raise ValueError("Wrong type for task_id %r", type(task_id))
        self.tid = task_id
        self.set_uuid(task_id)
        self.remote_ids = {}
        # set to True to disable self.sync() and avoid flooding on task edit
        self.sync_disabled = False
        self.content = ""
        if Task.DEFAULT_TASK_NAME is None:
            Task.DEFAULT_TASK_NAME = _("My new task")
        self.title = Task.DEFAULT_TASK_NAME
        # available status are: Active - Done - Dismiss - Note
        self.status = self.STA_ACTIVE

        self.added_date = Date.no_date()
        if newtask:
            self.added_date = Date(datetime.now())

        self.closed_date = Date.no_date()
        self.due_date = Date.no_date()
        self.start_date = Date.no_date()
        self.can_be_deleted = newtask
        # tags
        self.tags = []
        self.req = requester
        self.__main_treeview = requester.get_main_view()
        # If we don't have a newtask, we will have to load it.
        self.loaded = newtask
        # Should not be necessary with the new backends
#        if self.loaded:
#            self.req._task_loaded(self.tid)
        self.attributes = {}
        self._modified_update()

        # Setting the attributes related to repeating tasks.
        self.recurring = False  # type: bool
        self.recurring_term = None  # type: str
        self.recurring_updated_date = Date.no_date()
        self.inherit_recursion()

    def get_added_date(self):
        return self.added_date

    def set_added_date(self, value):
        self.added_date = Date(value)

    def is_loaded(self):
        return self.loaded

    def set_loaded(self, signal=True):
        # avoid doing it multiple times
        if not self.loaded:
            self.loaded = True

    def set_to_keep(self):
        self.can_be_deleted = False

    def is_new(self):
        return self.can_be_deleted

    def get_id(self):
        return str(self.tid)

    def set_uuid(self, value):
        self.uuid = str(value)

    def get_uuid(self):
        # NOTE: Transitional if switch, needed to add
        #      the uuid field to tasks created before
        #      adding this field to the task description.
        if self.uuid == "":
            self.set_uuid(uuid.uuid4())
            self.sync()
        return str(self.uuid)

    def get_title(self):
        return self.title

    def duplicate(self):
        """ Duplicates a task with a new ID """
        copy = self.req.ds.new_task()
        # Inherit the recurrency
        copy.set_recurring(True, self.recurring_term)
        nextdate = self.get_next_occurrence()
        copy.set_due_date(nextdate)

        copy.set_title(self.title)
        copy.content = self.content
        copy.tags = self.tags
        log.debug("Duppicating task %s as task %s",
                  self.get_id(), copy.get_id())
        return copy

    def duplicate_recursively(self):
        """
        Duplicates recursively all the task itself and its children while keeping the relationship
        """
        newtask = self.duplicate()
        if self.has_child():
            for c_tid in self.get_children():
                child = self.req.get_task(c_tid)
                if child.is_loaded():
                    newtask.add_child(child.duplicate_recursively())

        newtask.sync()
        return newtask.tid


    def set_title(self, title):
        """Set the tasks title. Returns True if title was changed."""

        if title:
            title = title.strip('\t\n')
        else:
            title = '(no title task)'

        # Avoid unnecessary syncing
        if title == self.title:
            return False
        else:
            self.title = title
            self.sync()
            return True

    def toggle_status(self):

        if self.status in [self.STA_DONE, self.STA_DISMISSED]:
            self.set_status(self.STA_ACTIVE)
        else:
            self.set_status(self.STA_DONE)

    def set_status(self, status, donedate=None, propagation=False, init=False):
        old_status = self.status
        self.can_be_deleted = False
        # No need to update children or whatever if the task is not loaded
        if status and self.is_loaded():
            # we first modify the status of the children
            # If Done, we set the done date
            if status in [self.STA_DONE, self.STA_DISMISSED]:
                for c in self.get_subtasks():
                    if c.get_status() in [self.STA_ACTIVE]:
                        c.set_status(status, donedate=donedate, propagation=True)

                # If the task is recurring, it must be duplicate with
                # another task id and the next occurence of the task
                # while preserving child/parent relations.
                # For a task to be duplicated, it must satisfy 3 rules.
                #   1- It is recurring.
                #   2- It has no parent or no recurring parent.
                #   3- It was directly marked as done (not by propagation from its parent).
                rules = [self.recurring, not propagation]
                if all(rules) and not self.is_parent_recurring():
                    # duplicate all the children
                    nexttask_tid = self.duplicate_recursively()
                    if self.has_parent():
                        for p_tid in self.get_parents():
                            par = self.req.get_task(p_tid)
                            if par.is_loaded() and par.get_status() in (self.STA_ACTIVE):
                                par.add_child(nexttask_tid)
                                par.sync()

            # If we mark a task as Active and that some parent are not
            # Active, we break the parent/child relation
            # It has no sense to have an active subtask of a done parent.
            # (old_status check is necessary to avoid false positive a start)
            elif status in [self.STA_ACTIVE] and\
                    old_status in [self.STA_DONE, self.STA_DISMISSED]:
                if self.has_parent():
                    for p_tid in self.get_parents():
                        par = self.req.get_task(p_tid)
                        if par.is_loaded() and par.get_status() in\
                                [self.STA_DONE, self.STA_DISMISSED]:
                            # we can either break the parent/child relationship
                            # self.remove_parent(p_tid)
                            # or restore the parent too
                            par.set_status(self.STA_ACTIVE)
                # We dont mark the children as Active because
                # They might be already completed after all

        # then the task itself
        if status:
            if not init:
                GLib.idle_add(self.req.emit, "status-changed", self.tid, self.status, status)
            self.status = status

        # Set closing date
        if status and status in [self.STA_DONE, self.STA_DISMISSED]:
            # to the specified date (if any)
            if donedate:
                self.closed_date = donedate
            # or to today
            else:
                self.closed_date = Date.today()
        self.sync()

    def get_status(self):
        return self.status

    def get_modified(self):
        return self.last_modified

    def set_modified(self, value):
        self.last_modified = Date(value)

    def recursive_sync(self):
        """Recursively sync the task and all task children. Defined"""
        self.sync()
        for sub_id in self.children:
            sub = self.req.get_task(sub_id)
            sub.recursive_sync()

    # ABOUT RECURRING TASKS
    # Like anything related to dates, repeating tasks are subtle and complex
    # when creating a new task, the due date is calculated from either the current date or
    # the start date, while we get the next occurrence of a task not from the current date but
    # from the due date itself.
    #
    # However when we are retrieving the task from the XML files, we should only set the
    # the recurring_term.

    def set_recurring(self, recurring: bool, recurring_term: str = None, newtask=False):
        """Sets a task as recurring or not, and its recurring term.

        There are 4 cases to acknowledge when setting a task to recurring:
          - if repeating but the term is invalid: it will be set to False.
          - if repeating and the term is valid: we set it to True.
          - if not repeating and the term is valid: we set the bool attr to True and set the term.
          - if not repeating and the term is invalid: we set it to False and keep the previous term.

        Setting a task as recurrent implies that the
        children of a recurrent task will be also
        set to recurrent and will inherit
        their parent's recurring term

        Args:
            recurring (bool): True if the task is recurring and False if not.
            recurring_term (str, optional): the recurring period of a task (every Monday, day..).
                                            Defaults to None.
            newtask (bool, optional): if this is a new task, we must set the due_date.
                                      Defaults to False.
        """
        def is_valid_term():
            """ Verify if the term is valid and returns the appropriate Due date.

            Return a tuple of (bool, Date)
            """
            if recurring_term is None:
                return False, None

            try:
                # If a start date is already set,
                # we should calculate the next date from that day.
                if self.start_date == Date.no_date():
                    start_from = Date(datetime.now())
                else:
                    start_from = self.start_date

                newdate = start_from.parse_from_date(recurring_term, newtask)
                return True, newdate
            except ValueError:
                return False, None

        self.recurring = recurring
        # We verifiy if the term passed is valid
        valid, newdate = is_valid_term()

        recurring_term = recurring_term if valid else None

        if self.recurring:
            if not valid:
                self.recurring_term = None
                self.recurring = False
            else:
                self.recurring_term = recurring_term
                self.recurring_updated_date = datetime.now()
                if newtask:
                    self.set_due_date(newdate)
        else:
            if valid:
                self.recurring_term = recurring_term
                self.recurring_updated_date = datetime.now()

        self.sync()
        # setting its children to recurrent
        if self.has_child():
            for c_tid in self.get_children():
                child = self.req.get_task(c_tid)
                if child.is_loaded() and child.get_status() in (self.STA_ACTIVE):
                    child.set_recurring(self.recurring, self.recurring_term)
                    if self.recurring:
                        child.set_due_date(newdate)

    def toggle_recurring(self):
        """ Toggle a task's recurrency ON/OFF. Use this function to toggle, not set_recurring"""
        # If there is no recurring_term, We assume it to recur every day.
        newtask = False
        if self.recurring_term is None:
            self.recurring_term = 'day'
            newtask = True

        self.set_recurring(not self.recurring, self.recurring_term, newtask)

    def get_recurring(self):
        return self.recurring

    def get_recurring_term(self):
        return self.recurring_term

    def get_recurring_updated_date(self):
        return self.recurring_updated_date

    def set_recurring_updated_date(self, date):
        self.recurring_updated_date = Date(date)

    def inherit_recursion(self):
        """ Inherits the recurrent state of the parent.
                If the task has a recurrent parent, it must be set to recur, itself.
        """
        if self.has_parent():
            for p_tid in self.get_parents():
                par = self.req.get_task(p_tid)
                if par.get_recurring() and par.is_loaded():
                    self.set_recurring(True, par.get_recurring_term())
                    self.set_due_date(par.due_date)
        else:
            self.set_recurring(False)


    def get_next_occurrence(self):
        """Calcutate the next occurrence of a recurring task

        To know which is the correct next occurrence there are two rules:
        - if the task was marked as done before or during the open period (before the duedate);
          in this case, we need to deal with the issue of tasks that recur on the same date.
          example: due_date is 09/09 and done_date is 09/09
        - if the task was marked after the due date, we need to figure out the next occurrence
          after the current date(today).

        Raises:
            ValueError: if the recurring_term is invalid

        Returns:
            Date: the next due date of a task
        """
        today = date.today()
        if today <= self.due_date:
            try:
                nextdate = self.due_date.parse_from_date(self.recurring_term, newtask=False)
                while nextdate <= self.due_date:
                    nextdate = nextdate.parse_from_date(self.recurring_term, newtask=False)
                return nextdate
            except Exception:
                raise ValueError(f'Invalid recurring term {self.recurring_term}')
        elif today > self.due_date:
            try:
                next_date = self.due_date.parse_from_date(self.recurring_term, newtask=False)
                while next_date < date.today():
                    next_date = next_date.parse_from_date(self.recurring_term, newtask=False)
                return next_date
            except Exception:
                raise ValueError(f'Invalid recurring term {self.recurring_term}')

    def is_parent_recurring(self):
        if self.has_parent():
            for p_tid in self.get_parents():
                p = self.req.get_task(p_tid)
                if p.is_loaded() and p.get_status() in (self.STA_ACTIVE) and p.get_recurring():
                    return True
        return False


    # ABOUT DUE DATES
    #
    # PLEASE READ THIS: although simple in appearance, handling task dates can
    # actually be subtle. Take the time to understand this if you plan to work
    # on the methods below.
    #
    # Due date is the date at which a task must be accomplished. Constraints
    # exist between a task's due date and its ancestor/children's due dates.
    #
    # Date constraints
    #
    # Those are the following:
    #   - children of a task cannot have a task due date that happens later
    #     than the task's due date
    #   - ancestors of a task cannot have a due that happens before the
    #     task's due date (this is the reverse constraint from the first one)
    #   - a task's start date cannot happen later than this task's due date
    #
    # Tasks with undefined or fuzzy due dates
    #
    # Task with no due date (="undefined" tasks) or tasks with fuzzy start/due
    # dates are not subject to constraints. Furthermore, they are
    # "transparent". Meaning that they let the constraints coming from their
    # children/parents pass through them. So, for instance, a children of
    # a task with an undefined or fuzzy task would be constrained by this
    # latter task's ancestors. Equally, the an ancestor from the same
    # undefined/fuzzy task would be constrained by the children due dates.
    #
    # Updating a task due date
    #
    # Whenever a task due date is changed, all ancestor/chldren of this task
    # *must* be updated according to the constraining rules. As said above,
    # constraints must go through tasks with undefined/fuzzy due dates too!
    #
    # Undefined/fuzzy task dates are NEVER to be updated. They are not
    # sensitive to constraint. If you want to now what constraint there is
    # on this task's due date though, you can obtain it by using
    # get_due_date_constraint method.
    def set_due_date(self, new_duedate):
        """Defines the task's due date."""

        def __get_defined_parent_list(task):
            """Recursively fetch a list of parents that have a defined due date
               which is not fuzzy"""
            parent_list = []
            for par_id in task.parents:
                par = self.req.get_task(par_id)
                if par.get_due_date().is_fuzzy():
                    parent_list += __get_defined_parent_list(par)
                else:
                    parent_list.append(par)
            return parent_list

        def __get_defined_child_list(task):
            """Recursively fetch a list of children that have a defined
               due date which is not fuzzy"""
            child_list = []
            for child_id in task.children:
                child = self.req.get_task(child_id)
                if child.get_due_date().is_fuzzy():
                    child_list += __get_defined_child_list(child)
                else:
                    child_list.append(child)
            return child_list

        old_due_date = self.due_date
        new_duedate_obj = Date(new_duedate)  # caching the conversion
        self.due_date = new_duedate_obj
        # If the new date is fuzzy or undefined, we don't update related tasks
        if not new_duedate_obj.is_fuzzy():
            # if some ancestors' due dates happen before the task's new
            # due date, we update them (except for fuzzy dates)
            for par in __get_defined_parent_list(self):
                if par.get_due_date() < new_duedate_obj:
                    par.set_due_date(new_duedate)
            # we must apply the constraints to the defined & non-fuzzy children
            # as well
            for sub in __get_defined_child_list(self):
                sub_duedate = sub.get_due_date()
                # if the child's due date happens later than the task's: we
                # update it to the task's new due date
                if sub_duedate > new_duedate_obj:
                    sub.set_due_date(new_duedate)
                # if the child's start date happens later than
                # the task's new due date, we update it
                # (except for fuzzy start dates)
                sub_startdate = sub.get_start_date()
                if not sub_startdate.is_fuzzy() and \
                        sub_startdate > new_duedate_obj:
                    sub.set_start_date(new_duedate)
        # If the date changed, we notify the change for the children since the
        # constraints might have changed
        if old_due_date != new_duedate_obj:
            self.recursive_sync()

    def get_due_date(self):
        """ Returns the due date, which always respects all constraints """
        return self.due_date

    def get_urgent_date(self):
        """
        Returns the most urgent due date among the task and its active subtasks
        """
        urgent_date = self.get_due_date()
        for subtask in self.get_subtasks():
            if subtask.get_status() == self.STA_ACTIVE:
                urgent_date = min(urgent_date, subtask.get_urgent_date())
        return urgent_date

    def get_due_date_constraint(self):
        """ Returns the most urgent due date constraint, following
            parents' due dates. Return Date.no_date() if no constraint
            is applied. """
        # Check out for constraints depending on date definition/fuzziness.
        strongest_const_date = self.due_date
        if strongest_const_date.is_fuzzy():
            for par_id in self.parents:
                par = self.req.get_task(par_id)
                par_duedate = par.get_due_date()
                # if parent date is undefined or fuzzy, look further up
                if par_duedate.is_fuzzy():
                    par_duedate = par.get_due_date_constraint()
                # if par_duedate is still undefined/fuzzy, all parents' due
                # dates are undefined or fuzzy: strongest_const_date is then
                # the best choice so far, we don't update it.
                if par_duedate.is_fuzzy():
                    continue
                # par_duedate is not undefined/fuzzy. If strongest_const_date
                # is still undefined or fuzzy, parent_duedate is the best
                # choice.
                if strongest_const_date.is_fuzzy():
                    strongest_const_date = par_duedate
                    continue
                # strongest_const_date and par_date are defined and not fuzzy:
                # we compare the dates
                if par_duedate < strongest_const_date:
                    strongest_const_date = par_duedate
        return strongest_const_date

    # ABOUT START DATE
    #
    # Start date is the date at which the user has decided to work or consider
    # working on this task.
    def set_start_date(self, fulldate):
        self.start_date = Date(fulldate)
        self.sync()

    def get_start_date(self):
        return self.start_date

    # ABOUT CLOSED DATE
    #
    # Closed date is the date at which the task has been closed (done or
    # dismissed). Closed date is not constrained and doesn't constrain other
    # dates.
    def set_closed_date(self, fulldate):
        self.closed_date = Date(fulldate)
        self.sync()

    def get_closed_date(self):
        return self.closed_date

    def get_days_left(self):
        return self.get_due_date().days_left()

    def get_days_late(self):
        due_date = self.get_due_date()
        if due_date == Date.no_date():
            return None
        closed_date = self.get_closed_date()
        return (closed_date - due_date).days

    def get_text(self):
        """ Return the content or empty string in case of None """
        if self.content:
            return str(self.content)
        else:
            return ""

    def get_excerpt(self, lines=0, char=0, strip_tags=False,
                    strip_subtasks=True):
        """
        get_excerpt return the beginning of the content of the task.
        If "lines" is provided and different than 0, it return the number X
        of line (or the whole content if it contains less lines)
        If "char" is provided, it returns the X first chars of content (or the
        whole contents if it contains less char)
        If both char and lines are provided, the shorter one is returned.
        If none of them are provided (or if they are 0), this function is
        equivalent to get_text with with all XML stripped down.
        Warning: all markup informations are stripped down. Empty lines are
        also removed
        """
        # defensive programmation to avoid returning None
        if self.content:
            txt = self.content

            # Prevent issues with & in content
            txt = saxutils.escape(txt)
            txt = txt.strip()

            if strip_tags:
                for tag in self.get_tags_name():
                    txt = (txt.replace(f'@{tag}, ', '')
                              .replace(f'@{tag},', '')
                              .replace(f'@{tag}', ''))

            if strip_subtasks:
                txt = re.sub(r'\{\!.+\!\}', '', txt)

            # Strip blank lines and get desired amount of lines
            txt = [line for line in txt.splitlines() if line]
            txt = txt[:lines]
            txt = '\n'.join(txt)

            # We keep the desired number of char
            if char > 0:
                txt = txt[:char]
            return txt
        else:
            return ""

    def __strip_content(self, element, strip_subtasks=False):
        txt = ""
        if element:
            for n in element.childNodes:
                if n.nodeType == n.ELEMENT_NODE:
                    if strip_subtasks and n.tagName == 'subtask':
                        if txt[-2:] == '→ ':
                            txt = txt[:-2]
                    else:
                        txt += self.__strip_content(n, strip_subtasks)
                elif n.nodeType == n.TEXT_NODE:
                    txt += n.nodeValue
        return txt

    def set_text(self, texte):
        self.can_be_deleted = False
        self.content = html.unescape(str(texte))

    # SUBTASKS ###############################################################
    def new_subtask(self):
        """Add a newly created subtask to this task. Return the task added as
        a subtask
        """
        subt = self.req.new_task(newtask=True)
        # we use the inherited childrens
        self.add_child(subt.get_id())
        return subt

    def add_child(self, tid):
        """Add a subtask to this task

        @param child: the added task
        """
        log.debug("adding child %s to task %s", tid, self.get_id())
        self.can_be_deleted = False
        # the core of the method is in the TreeNode object
        TreeNode.add_child(self, tid)
        # now we set inherited attributes only if it's a new task
        child = self.req.get_task(tid)
        if self.is_loaded() and child and child.can_be_deleted:
            # If the the child is repeating no need to change the date
            if not child.get_recurring():
                child.set_start_date(self.get_start_date())
                child.set_due_date(self.get_due_date())
            for t in self.get_tags():
                child.add_tag(t.get_name())

            child.inherit_recursion()

        self.sync()
        return True

    def remove_child(self, tid):
        """Removed a subtask from the task.

        @param tid: the ID of the task to remove
        """
        c = self.req.get_task(tid)
        c.remove_parent(self.get_id())
        if c.can_be_deleted:
            self.req.delete_task(tid)
            self.sync()
            return True
        else:
            return False

    # FIXME: remove this function and use liblarch instead.
    def get_subtasks(self):
        tree = self.get_tree()
        return [tree.get_node(node_id) for node_id in self.get_children()]

    def set_parent(self, parent_id):
        """Update the task's parent. Refresh due date constraints."""
        TreeNode.set_parent(self, parent_id)
        if parent_id is not None:
            par = self.req.get_task(parent_id)
            par_duedate = par.get_due_date_constraint()
            if not par_duedate.is_fuzzy() and \
                not self.due_date.is_fuzzy() and \
                    par_duedate < self.due_date:
                self.set_due_date(par_duedate)
            self.inherit_recursion()
        self.recursive_sync()

    def set_attribute(self, att_name, att_value, namespace=""):
        """Set an arbitrary attribute.

        @param att_name: The name of the attribute.
        @param att_value: The value of the attribute. Will be converted to a
            string.
        """
        val = str(att_value)
        self.attributes[(namespace, att_name)] = val
        self.sync()

    def get_attribute(self, att_name, namespace=""):
        """Get the attribute C{att_name}.

        Returns C{None} if there is no attribute matching C{att_name}.
        """
        return self.attributes.get((namespace, att_name), None)

    def sync(self):
        if self.sync_disabled:
            return self.is_loaded()
        self._modified_update()
        if self.is_loaded():
            # This is a liblarch call to the TreeNode ancestor
            self.modified()
            return True
        return False

    def _modified_update(self):
        """
        Updates the modified timestamp
        """
        self.last_modified = datetime.now()

# TAG FUNCTIONS ##############################################################
    def get_tags_name(self):
        # Return a copy of the list of tags. Not the original object.
        return list(self.tags)

    # return a copy of the list of tag objects
    def get_tags(self):
        tags = []
        for tname in self.tags:
            tag = self.req.get_tag(tname)
            if not tag:
                tag = self.req.new_tag(tname)
            tags.append(tag)
        return tags

    def rename_tag(self, old, new):
        eold = saxutils.escape(saxutils.unescape(old))
        enew = saxutils.escape(saxutils.unescape(new))
        self.content = self.content.replace(eold, enew)
        oldt = self.req.get_tag(old)
        self.remove_tag(old)
        oldt.modified()
        self.tag_added(new)
        self.req.get_tag(new).modified()
        self.sync()

    def tag_added_by_id(self, tid):
        """Add a tag by its ID"""

        tag = self.req.ds.get_tag_by_id(tid)

        if tag:
            self.tag_added(tag.get_name())

    def tag_added(self, tagname):
        """
        Adds a tag. Does not add '@tag' to the contents. See add_tag
        """
        if tagname not in self.tags:
            self.tags.append(tagname)
            if self.is_loaded():
                for child in self.get_subtasks():
                    if child.can_be_deleted:
                        child.tag_added(tagname)

                tag = self.req.get_tag(tagname)
                if not tag:
                    tag = self.req.new_tag(tagname)
                tag.modified()
            return True

    def add_tag(self, tagname):
        "Add a tag to the task and insert '@tag' into the task's content"

        if self.tag_added(tagname):
            c = self.content
            tagname = html.escape(tagname)
            tagname = '@' + tagname if not tagname.startswith('@') else tagname

            if not c:
                # don't need a separator if it's the only text
                sep = ''
            elif c.startswith('@'):
                # if content starts with a tag, make a comma-separated list
                sep = ', '
            else:
                # other text at the beginning, so put the tag on its own line
                sep = '\n\n'

            self.content = f'{tagname}{sep}{c}'
            # we modify the task internal state, thus we have to call for a
            # sync

            self.sync()

    # remove by tagname
    def remove_tag(self, tagname):
        modified = False
        if tagname in self.tags:
            self.tags.remove(tagname)
            modified = True
            for child in self.get_subtasks():
                if child.can_be_deleted:
                    child.remove_tag(tagname)
        self.content = self._strip_tag(self.content, tagname)
        if modified:
            tag = self.req.get_tag(tagname)
            # The ViewCount of the tag still doesn't know that
            # the task was removed. We need to update manually
            if tag:
                tag.update_task(self.get_id())
                tag.modified()

    def _strip_tag(self, text, tagname, newtag=''):
        if tagname.startswith('@'):
            inline_tag = tagname[1:]
        else:
            inline_tag = tagname

        return (text
                .replace(f'@{tagname}\n\n', newtag)
                .replace(f'@{tagname}\n', newtag)
                .replace(f'@{tagname}, ', newtag)
                .replace(f'@{tagname}', newtag)
                .replace(f'{tagname}\n\n', newtag)
                .replace(f'{tagname}, ', newtag)
                .replace(f'{tagname},', inline_tag)
                # don't forget a space a the end
                .replace(f'{tagname}', inline_tag))

    # tag_list is a list of tags names
    # return true if at least one of the list is in the task
    def has_tags(self, tag_list=None, notag_only=False):
        # recursive function to explore the tags and its children
        def children_tag(tagname):
            toreturn = False
            if tagname in self.tags:
                toreturn = True
            else:
                tag = self.req.get_tag(tagname)
                if tag:
                    for tagc_name in tag.get_children():
                        if not toreturn:
                            toreturn = children_tag(tagc_name)
            return toreturn

        # We want to see if the task has no tags
        toreturn = False
        if notag_only:
            toreturn = self.tags == []
        # Here, the user ask for the "empty" tag
        # And virtually every task has it.
        elif tag_list == [] or tag_list is None:
            toreturn = True
        elif tag_list:
            for tagname in tag_list:
                if not toreturn:
                    toreturn = children_tag(tagname)
        else:
            # Well, if we don't filter on tags or notag, it's true, of course
            toreturn = True
        return toreturn

    def __str__(self):
        return '<Task title="%s" id="%s" status="%s" tags="%s" added="%s" recurring="%s">' % (
            self.title,
            self.tid,
            self.status,
            self.tags,
            self.added_date,
            self.recurring)

    __repr__ = __str__


class DisabledSyncCtx:
    """Context manager disabling GTG.core.task.Task.sync()
    and firing one only sync on __exit__"""

    def __init__(self, task: Task, sync_on_exit: bool = True):
        self.task = task
        self.sync_on_exit = sync_on_exit

    def __enter__(self):
        self.task.sync_disabled = True
        return self.task

    def __exit__(self, *args, **kwargs):
        self.task.sync_disabled = False
        if self.sync_on_exit:
            self.task.sync()
