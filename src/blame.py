# coding: utf-8
#
# Copyright © 2012-2017 Ejwa Software. All rights reserved.
#
# This file is part of gitinspector.
#
# gitinspector is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# gitinspector is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with gitinspector. If not, see <http://www.gnu.org/licenses/>.

import datetime
import multiprocessing
import re
import subprocess
import threading
from .changes import FileDiff
from . import extensions, filtering, format, interval, languages, terminal

NUM_THREADS = multiprocessing.cpu_count()

class BlameEntry(object):
	rows = 0
	skew = 0 # Used when calculating average code age.
	comments = 0

_thread_lock = threading.BoundedSemaphore(NUM_THREADS)
_blame_lock = threading.Lock()

AVG_DAYS_PER_MONTH = 30.4167

class BlameThread(threading.Thread):
	def __init__(self, useweeks, changes, blame_command, extension, blames, filename):
		_thread_lock.acquire() # Lock controlling the number of threads running
		threading.Thread.__init__(self)

		self.useweeks = useweeks
		self.changes = changes
		self.blame_command = blame_command
		self.extension = extension
		self.blames = blames
		self.filename = filename

		self.is_inside_comment = False

	def _clear_blamechunk_info(self):
		self.blamechunk_email = None
		self.blamechunk_is_last = False
		self.blamechunk_is_prior = False
		self.blamechunk_revision = None
		self.blamechunk_time = None

	def _handle_blamechunk_content(self, content):
		author = None
		(comments, self.is_inside_comment) = languages.handle_comment_block(self.is_inside_comment, self.extension, content)

		if self.blamechunk_is_prior and interval.get_since():
			return
		try:
			author = self.changes.get_latest_author_by_email(self.blamechunk_email)
		except KeyError:
			return

		if not filtering.set_filtered(author, "author") and not \
		       filtering.set_filtered(self.blamechunk_email, "email") and not \
		       filtering.set_filtered(self.blamechunk_revision, "revision"):

			_blame_lock.acquire() # Global lock used to protect calls from here...

			if self.blames.get((author, self.filename), None) == None:
				self.blames[(author, self.filename)] = BlameEntry()

			self.blames[(author, self.filename)].comments += comments
			self.blames[(author, self.filename)].rows += 1

			if (self.blamechunk_time - self.changes.first_commit_date).days > 0:
				self.blames[(author, self.filename)].skew += ((self.changes.last_commit_date - self.blamechunk_time).days /
				                                             (7.0 if self.useweeks else AVG_DAYS_PER_MONTH))

			_blame_lock.release() # ...to here.

	def run(self):
		git_blame_r = subprocess.Popen(self.blame_command, stdout=subprocess.PIPE).stdout
		rows = git_blame_r.readlines()
		git_blame_r.close()

		self._clear_blamechunk_info()

		#pylint: disable=W0201
		for j in range(0, len(rows)):
			row = rows[j].decode("utf-8", "replace").strip()
			keyval = row.split(" ", 2)

			if self.blamechunk_is_last:
				self._handle_blamechunk_content(row)
				self._clear_blamechunk_info()
			elif keyval[0] == "boundary":
				self.blamechunk_is_prior = True
			elif keyval[0] == "author-mail":
				self.blamechunk_email = keyval[1].lstrip("<").rstrip(">")
			elif keyval[0] == "author-time":
				self.blamechunk_time = datetime.date.fromtimestamp(int(keyval[1]))
			elif keyval[0] == "filename":
				self.blamechunk_is_last = True
			elif Blame.is_revision(keyval[0]):
				self.blamechunk_revision = keyval[0]

		_thread_lock.release() # Lock controlling the number of threads running

PROGRESS_TEXT = "Checking how many rows belong to each author (2 of 2): {0:.0f}%"

class Blame(object):
	def __init__(self, repo, hard, useweeks, changes):
		self.blames = {}
		ls_tree_p = subprocess.Popen(["git", "ls-tree", "--name-only", "-r", interval.get_ref()],
		                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
		lines = ls_tree_p.communicate()[0].splitlines()
		ls_tree_p.stdout.close()

		if ls_tree_p.returncode == 0:
			progress_text = PROGRESS_TEXT

			if repo != None:
				progress_text = "[%s] " % repo.name + progress_text

			for i, row in enumerate(lines):
				row = row.strip().decode("unicode_escape", "ignore")
				row = row.encode("latin-1", "replace")
				row = row.decode("utf-8", "replace").strip("\"").strip("'").strip()

				if FileDiff.get_extension(row) in extensions.get_located() and \
				   FileDiff.is_valid_extension(row) and not filtering.set_filtered(FileDiff.get_filename(row)):
					blame_command = filter(None, ["git", "blame", "--line-porcelain", "-w"] + \
							(["-C", "-C", "-M"] if hard else []) +
					                [interval.get_since(), interval.get_ref(), "--", row])
					thread = BlameThread(useweeks, changes, blame_command, FileDiff.get_extension(row),
					                     self.blames, row.strip())
					thread.daemon = True
					thread.start()

					if format.is_interactive_format():
						terminal.output_progress(progress_text, i, len(lines))

			# Make sure all threads have completed.
			for i in range(0, NUM_THREADS):
				_thread_lock.acquire()

			# We also have to release them for future use.
			for i in range(0, NUM_THREADS):
				_thread_lock.release()

	def __iadd__(self, other):
		try:
			self.blames.update(other.blames)
			return self
		except AttributeError:
			return other

	@staticmethod
	def is_revision(string):
		revision = re.search("([0-9a-f]{40})", string)

		if revision == None:
			return False

		return revision.group(1).strip()

	@staticmethod
	def get_stability(author, blamed_rows, changes):
		if author in changes.get_authorinfo_list():
			author_insertions = changes.get_authorinfo_list()[author].insertions
			return 100 if author_insertions == 0 else 100.0 * blamed_rows / author_insertions
		return 100

	@staticmethod
	def get_time(string):
		time = re.search(r" \(.*?(\d\d\d\d-\d\d-\d\d)", string)
		return time.group(1).strip()

	def get_summed_blames(self):
		summed_blames = {}
		for i in self.blames.items():
			if summed_blames.get(i[0][0], None) == None:
				summed_blames[i[0][0]] = BlameEntry()

			summed_blames[i[0][0]].rows += i[1].rows
			summed_blames[i[0][0]].skew += i[1].skew
			summed_blames[i[0][0]].comments += i[1].comments

		return summed_blames
