# coding: utf-8
#
# Copyright © 2014 Ejwa Software. All rights reserved.
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

import os
import shutil
import subprocess
import sys
import tempfile

from urllib.parse import urlparse

_cloned_paths = []

def create(url):
	class Repository(object):
		def __init__(self, name, location):
			self.name = name
			self.location = location

	parsed_url = urlparse(url)

	if parsed_url.scheme == "file" or parsed_url.scheme == "git" or parsed_url.scheme == "http" or \
	   parsed_url.scheme == "https" or parsed_url.scheme == "ssh":
		path = tempfile.mkdtemp(suffix=".gitinspector")
		git_clone = subprocess.Popen(["git", "clone", url, path], stdout=sys.stderr)
		git_clone.wait()

		if git_clone.returncode != 0:
			sys.exit(git_clone.returncode)

		_cloned_paths.append(path)
		return Repository(os.path.basename(parsed_url.path), path)

	return Repository(None, os.path.abspath(url))

def delete():
	for path in _cloned_paths:
		shutil.rmtree(path, ignore_errors=True)
