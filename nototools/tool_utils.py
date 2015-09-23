# Copyright 2015 Google Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Some common utilities for tools to use."""

import contextlib
import datetime
import glob
import os
import os.path as path
import re
import shutil
import subprocess
import time
import zipfile

from nototools import notoconfig

@contextlib.contextmanager
def temp_chdir(path):
  """Usage: with temp_chdir(path):
    do_something
  """
  saved_dir = os.getcwd()
  try:
    os.chdir(path)
    yield
  finally:
    os.chdir(saved_dir)


noto_re = re.compile(r'\[(tools|fonts|emoji|cjk|adobe|mti|afdko)\](.*)')
def resolve_path(somepath):
  """Resolve a path that might start with noto path shorthand. If
  the path is empty, is '-', or the shorthand is not defined,
  returns None. Example: '[fonts]/hinted'."""

  if not somepath or somepath == '-':
    return None
  m = noto_re.match(somepath)
  if m:
    base, rest = m.groups()
    if base == 'adobe':
      key = 'adobe_data'
    elif base == 'mti':
      key = 'monotype_data'
    elif base == 'afdko':
      key = 'afdko'
    else:
      key = 'noto_' + base
    if not key in notoconfig.values:
      print 'notoconfig has no entry for %s' % key
      return None
    base = notoconfig.values.get(key)
    while rest.startswith(path.sep):
      rest = rest[len(path.sep):]
    somepath = path.join(base, rest)
  return path.realpath(path.abspath(path.expanduser(somepath)))


def check_dir_exists(dirpath):
  if not os.path.isdir(dirpath):
    raise ValueError('%s does not exist or is not a directory' % dirpath)


def check_file_exists(filepath):
  if not os.path.isfile(filepath):
    raise ValueError('%s does not exist or is not a file' % filepath)


def ensure_dir_exists(path):
  path = os.path.realpath(path)
  if not os.path.isdir(path):
    if os.path.exists(path):
      raise ValueError('%s exists and is not a directory' % path)
    print "making '%s'" % path
    os.makedirs(path)
  return path


def generate_zip_with_7za(root_dir, file_paths, archive_path):
  """file_paths is a list of files relative to root_dir, these will be the names
  in the archive at archive_path."""

  arg_list = ['7za', 'a', archive_path, '-tzip', '-mx=7', '-bd', '--']
  arg_list.extend(file_paths)
  with temp_chdir(root_dir):
    # capture but discard output
    subprocess.check_output(arg_list)


def generate_zip_with_7za_from_filepairs(pairs, archive_path):
  """Pairs are source/destination path pairs. The source will be put into the
  zip with name destination."""

  staging_dir = '/tmp/stage_7za'
  if os.path.exists(staging_dir):
    shutil.rmtree(staging_dir)
  os.makedirs(staging_dir)

  pair_map = {}
  for source, dest in pairs:
    if not source.endswith(dest):
      staging_source = os.path.join(staging_dir, dest)
      shutil.copyfile(source, staging_source)
      source_root = staging_dir
    else:
      source_root = source[:-len(dest)]
    if source_root not in pair_map:
      pair_map[source_root] = set()
    pair_map[source_root].add(dest)
  for source_root, dest_set in pair_map.iteritems():
    generate_zip_with_7za(source_root, sorted(dest_set), archive_path)


def dos2unix(root_dir, glob_list):
  """Convert dos line endings to unix ones in place."""
  with temp_chdir(root_dir):
    for g in glob_list:
      file_list = glob.glob(g)
      if file_list:
        subprocess.check_call(['dos2unix', '-k', '-q', '-o'] + file_list)


def zip_extract_with_timestamp(zippath, dstdir):
  zip = zipfile.ZipFile(zippath)
  with temp_chdir(dstdir):
    for info in zip.infolist():
      zip.extract(info.filename)
      # of course, time zones mess this up, so don't expect precision
      date_time = time.mktime(info.date_time + (0, 0, -1))
      os.utime(info.filename, (date_time, date_time))


def git_mv(repo, old, new):
  """Rename old to new in repo"""
  with temp_chdir(repo):
    return subprocess.check_output(
        ['git', 'mv', old, new])


def git_file_lastlog(repo, filepath):
  """Return a string containing the short hash, date, author email, and title
  of most recent commit of filepath, separated by tab."""
  with temp_chdir(repo):
    return subprocess.check_output(
        ['git', 'log', '-n', '1', '--format=%h\t%ad\t%ae\t%s', '--date=short',
         '--', filepath])


def get_tool_generated(repo, subdir, commit_title_prefix='Updated by tool'):
  """
  Return a list of the names of tool-generated files in the provided directory.
  The idea is that when we check in files that are generated by a tool, the
  commit will start with the given prefix.  If a files' most recent log entry
  matches this, it means that we've not applied patches or fixes to the file
  since it was generated, so we can overwrite it with new tool-generated data.

  The motivation for this is mantaining the sample texts.  The original source
  for most of these is UDHR data, but subsequently we have fixed things in
  some of the samples.  We generally do not want to blindly overwrite these
  fixes, but do want to be able to regenerate the samples if we get new source
  data.
  """
  tool_generated_files = []
  for f in os.listdir(path.join(repo, subdir)):
    relpath = path.join(subdir, f)
    commit, date, author, title = git_file_lastlog(repo, relpath).split('\t')
    if title.startswith(commit_title_prefix):
      tool_generated_files.append(f)
  return tool_generated_files


def git_get_branch(repo):
  with temp_chdir(repo):
    return subprocess.check_output(['git', 'symbolic-ref', '--short', 'HEAD']).strip()


def git_is_clean(repo):
  """Ensure there are no unstaged or uncommitted changes in the repo."""

  result = True
  with temp_chdir(repo):
    subprocess.check_call(['git', 'update-index', '-q', '--ignore-submodules', '--refresh'])
    if subprocess.call(['git', 'diff-files', '--quiet', '--ignore-submodules', '--']):
      print 'There are unstaged changes in the noto branch:'
      subprocess.call(['git', 'diff-files', '--name-status', '-r', '--ignore-submodules', '--'])
      result = False
    if subprocess.call(
        ['git', 'diff-index', '--cached', '--quiet', 'HEAD', '--ignore-submodules', '--']):
      print 'There are uncommitted changes in the noto branch:'
      subprocess.call(
        ['git', 'diff-index', '--cached', '--name-status', '-r', 'HEAD', '--ignore-submodules', '--'])
      result = False
  return result


def git_add_all(repo_subdir):
  """Add all changed, deleted, and new files in subdir to the staging area."""
  # git can now add everything, even removed files
  with temp_chdir(repo_subdir):
    subprocess.check_call(['git', 'add', '--', '.'])


def svn_get_version(repo):
  with temp_chdir(repo):
    version_string = subprocess.check_output(['svnversion', '-c']).strip()
    colon_index = version_string.find(':')
    if colon_index >= 0:
      version_string = version_string[colon_index + 1:]
  return version_string


def svn_update(repo):
  with temp_chdir(repo):
    subprocess.check_call(['svn', 'up'], stderr=subprocess.STDOUT)
