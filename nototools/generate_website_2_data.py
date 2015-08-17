#!/usr/bin/python
# -*- coding: UTF-8 -*-
#
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

"""Generate data files for the Noto website."""

from __future__ import division

__author__ = 'roozbeh@google.com (Roozbeh Pournader)'

import argparse
import codecs
import collections
import csv
import json
import locale
import os
from os import path
import shutil
import subprocess
import xml.etree.cElementTree as ElementTree

from fontTools import ttLib

from nototools import cldr_data
from nototools import coverage
from nototools import create_image
from nototools import extra_locale_data
from nototools import lang_data
from nototools import notoconfig
from nototools import noto_fonts
from nototools import tool_utils
from nototools import unicode_data

TOOLS_DIR = notoconfig.values['noto_tools']
FONTS_DIR = notoconfig.values['noto_fonts']
CJK_DIR = notoconfig.values['noto_cjk']

CLDR_DIR = path.join(TOOLS_DIR, 'third_party', 'cldr')

LAT_LONG_DIR = path.join(TOOLS_DIR, 'third_party', 'dspl')
SAMPLE_TEXT_DIR = path.join(TOOLS_DIR, 'sample_texts')

APACHE_LICENSE_LOC = path.join(FONTS_DIR, 'LICENSE')
SIL_LICENSE_LOC = path.join(CJK_DIR, 'LICENSE')

def get_script_to_family_ids(family_map):
  """The keys in the returned map are all the supported scripts."""
  script_to_family_ids = collections.defaultdict(set)
  for key in family_map:
    script = family_map[key].rep_member.script
    if script == 'LGC':
      for script in ['Latn', 'Grek', 'Cyrl']:
        script_to_family_ids[script].add(key)
    else:
      script_to_family_ids[script].add(key)
  return script_to_family_ids


def get_family_id_to_lang_scrs(lang_scrs, script_to_family_ids):
  family_id_to_lang_scrs = collections.defaultdict(set)
  for lang_scr in lang_scrs:
    lang, script = lang_scr.split('-')
    family_ids = script_to_family_ids[script]
    for family_id in family_ids:
      family_id_to_lang_scrs[family_id].add(lang_scr)

  # Nastaliq patches:
  # Additionally map some languages in Arab script to Nastaliq ('Aran')
  nastaliq_lang_scrs = family_id_to_lang_scrs['nastaliq-aran']
  for lang_scr in ['bal-Arab', 'hnd-Arab', 'hno-Arab', 'ks-Arab', 'lah-Arab',
                   'pa-Arab', 'skr-Arab', 'ur-Arab']:
    if not lang_scr in lang_scrs:
      print 'Map nastaliq: %s not found' % lang_scr
    else:
      print 'added %s to nastaliq' % lang_scr
      nastaliq_lang_scrs.add(lang_scr)

  # Kufi patches:
  # - Kufi is broken for Urdu Heh goal (issue #34)
  # - Kufi doesn't support all characters needed for Khowar
  # - Kufi doesn't support all characters needed for Kashmiri
  kufi_lang_scrs = family_id_to_lang_scrs['kufi-arab']
  for lang_scr in ['ur-Arab', 'khw-Arab', 'ks-Arab']:
    if not lang_scr in lang_scrs:
      print 'Patch kufi: %s not found' % lang_scr
    else:
      kufi_lang_scrs.remove(lang_scr)
      print 'removed %s from kufi' % lang_scr
      if not kufi_lang_scrs:
        break


  for f, ls in family_id_to_lang_scrs.iteritems():
    if not ls:
      print '!family %s has no lang' % f

  return family_id_to_lang_scrs


def get_family_id_to_lang_scr_to_sample_key(family_id_to_lang_scrs,
                                           families,
                                           lang_scr_to_sample_infos):
    """For each lang_scr + family combination, determine which sample to use from
    those available for the lang_scr.  If the family can't display any of the
    samples, report an error, the lang will not be added to those supported
    by the family.  If the family supports no languages, also report an error.

    The returned value is a tuple:
    - a map from family_id to another map, which is:
      - a map from lang_scr to sample_key
    - a map from sample_key to sample info
    """

    family_id_to_lang_scr_to_sample_key = {}
    sample_key_to_info = {}

    tested_keys = set()
    failed_keys = set()

    for family_id in sorted(family_id_to_lang_scrs):
      lang_scr_to_sample_key = {}
      for lang_scr in sorted(family_id_to_lang_scrs[family_id]):
        sample_infos = lang_scr_to_sample_infos[lang_scr]
        assert len(sample_infos) > 0

        for info in sample_infos:
          sample = info[1]
          sample_key = info[3]

          full_key = sample_key + '-' + family_id
          if full_key in tested_keys:
            if full_key in failed_keys:
              print 'family %s already rejected sample %s (lang %s)' % (family_id, sample_key, lang_scr)
              continue
          else:
            failed_cps = set()
            tested_keys.add(full_key)
            charset = families[family_id].charset
            for cp in sample:
              if ord(cp) in [0xa, 0x28, 0x29, 0x2c, 0x2d, 0x2e, 0x3b, 0x5b, 0x5d, 0x2010]:
                continue
              if ord(cp) not in charset:
                failed_cps.add(ord(cp))

            if failed_cps:
              print 'family %s rejects sample %s for lang %s:\n  %s' % (
                  family_id, sample_key, lang_scr,
                  '\n  '.join('%04x (%s)' % (cp, unichr(cp)) for cp in sorted(failed_cps)))
              failed_keys.add(full_key)
              continue

          print 'family %s accepts sample %s for lang %s' % (family_id, sample_key, lang_scr)

          sample_key_for_lang = sample_key
          if sample_key not in sample_key_to_info:
            sample_key_to_info[sample_key] = info
          break

        if not sample_key_for_lang:
          print '%s has no sample to display in %s' % (lang_scr, family_id)
        else:
          lang_scr_to_sample_key[lang_scr] = sample_key_for_lang

      if not lang_scr_to_sample_key:
        print '!%s can display no samples for any lang of %s' % (
            family_id, ', '.join(sorted(family_id_to_lang_scrs[family_id])))
      else:
        family_id_to_lang_scr_to_sample_key[family_id] = lang_scr_to_sample_key

    return (family_id_to_lang_scr_to_sample_key, sample_key_to_info)


def get_region_to_family_ids(script_to_family_ids):
  region_to_family_ids = collections.defaultdict(set)
  for region in cldr_data.known_regions():
    if region == 'ZZ':
      continue
    if len(region) > 2:
      print 'skipping region %s' % region
      continue
    lang_scripts = cldr_data.region_to_lang_scripts(region)
    for lang_script in lang_scripts:
      lang, script = lang_script.split('-')
      if script == 'Kana':
        print 'remap %s to use Jpan script' % lang_script
        script = 'Jpan'
      if not script in script_to_family_ids:
        print 'unsupported script %s for lang %s in region %s' % (script, lang, region)
      else:
        families = script_to_family_ids[script]
        region_to_family_ids[region].update(families)
  return region_to_family_ids


def get_supported_lang_scrs(family_id_to_lang_scr_to_sample_key):
  supported_lang_scrs = set()
  for lang_scrs in family_id_to_lang_scr_to_sample_key.itervalues():
    supported_lang_scrs.union(lang_scrs)
  return supported_lang_scrs


def get_lang_scr_sort_order(lang_scrs):
  """Return a sort order for lang_scrs based on the english name, but clustering related
  languages."""
  def lang_key(lang_scr):
    name = lang_data.lang_script_to_names(lang_scr)[0]
    if name.endswith (' script)'):
      ix = name.rfind('(') - 1
      script_sfx = ' ' + name[ix + 2: len(name) - 8]
      name = name[:ix]
    else:
      script_sfx = ''

    key = name
    for prefix in ['Ancient', 'Central', 'Eastern', 'Lower', 'Middle', 'North',
                   'Northern', 'Old', 'Southern', 'Southwestern', 'Upper',
                   'West', 'Western']:
      if name.startswith(prefix + ' '):
        key = name[len(prefix) + 1:] + ' ' + name[:len(prefix)]
        break

    for cluster in ['Arabic', 'French', 'Chinese', 'English', 'German', 'Hindi',
                    'Malay', 'Nahuatl', 'Tamazight', 'Thai']:
      if name.find(cluster) != -1:
        key = cluster + '-' + name
        break

    return key + script_sfx

  sorted_lang_scrs = list(lang_scrs)
  sorted_lang_scrs.sort(key=lang_key)
  n = 0
  tag_order = {}
  for lang_scr in sorted_lang_scrs:
    # print '%10s: %s' % (lang_scr, cldr_data.get_english_language_name(lang_scr))
    tag_order[lang_scr] = n
    n += 1
  return tag_order


def get_family_id_to_regions(region_to_family_ids, families):
  family_id_to_regions = {}
  for family_id in families:
    family_id_to_regions[family_id] = set()
  for region, family_ids in region_to_family_ids.iteritems():
    for family_id in family_ids:
      family_id_to_regions[family_id].add(region)
  return family_id_to_regions


def get_charset_info(charset):
  """Returns an encoding of the charset as pairs of lengths of runs of chars to skip and
  chars to include.  Each length is written as length - 1 in hex-- except when length ==
  1, which is written as the empty string-- and separated from the next length by a comma.
  Thus successive commas indicate a length of 1, a 1 indicates a length of 2, and so on.
  Since the minimum representable length is 1, the base is -1 so that the first run (a
  skip) of 1 can be output as a comma to then start the first included character at 0 if
  need be.  Only as many pairs of values as are needed to encode the last run of included
  characters."""

  ranges = coverage.convert_set_to_ranges(charset)
  prev = -1
  range_list = []
  for start, end in ranges:
    range_len = start - 1 - prev
    if range_len > 0:
      range_list.append('%x' % range_len)
    else:
      range_list.append('')
    range_len = end - start
    if range_len > 0:
      range_list.append('%x' % range_len)
    else:
      range_list.append('')
    prev = end + 1
  return ','.join(range_list)


def get_sample_from_sample_file(lang_scr_ext):
  filepath = path.join(SAMPLE_TEXT_DIR, lang_scr_ext + '.txt')
  if path.exists(filepath):
    return unicode(open(filepath).read().strip(), 'UTF-8')
  return None


ATTRIBUTION_DATA = {}

def get_attribution(lang_scr_ext):
  if not ATTRIBUTION_DATA:
    attribution_path = path.join(TOOLS_DIR, 'sample_texts', 'attributions.txt')
    with open(attribution_path, 'r') as f:
      data = f.readlines()
    for line in data:
      line = line.strip()
      if not line or line[0] == '#':
        continue
      tag, attrib = line.split(':')
      ATTRIBUTION_DATA[tag.strip()] = attrib.strip()
    print 'read %d lines of attribution data' % len(ATTRIBUTION_DATA)
  try:
    return ATTRIBUTION_DATA[lang_scr_ext + '.txt']
  except KeyError:
    print 'no attribution for %s' % lang_scr_ext
    return 'none'


EXEMPLAR_CUTOFF_SIZE = 60

def sample_text_from_exemplar(exemplar):
  exemplar = [c for c in exemplar
                if unicode_data.category(c[0])[0] in 'LNPS']
  exemplar = exemplar[:EXEMPLAR_CUTOFF_SIZE]
  return ' '.join(exemplar)


def get_sample_infos(lang_scr, rtl):
  """Return a list of tuples of:
  - true if rtl, false if not
  - a short sample text string
  - an attribution key, one of
    UN: official UN translation, needs attribution
    other: not an official UN translation, needs non-attribution
    original: public domain translation, does not need attribution
    none: we have no attribution info on this, does not need attribution
  - source key.
  The list is in order of priority: udhr samples, exemplars for the language,
  sample text for the script, exemplars for the script."""

  assert '-' in lang_scr

  sample_infos = []
  sample_text = get_sample_from_sample_file(lang_scr + '_udhr')
  if sample_text is not None:
    src_key = lang_scr + '_udhr'
    attr = get_attribution(src_key)
    sample_infos.append((rtl, sample_text, attr, src_key))

  exemplar, src_key = cldr_data.get_exemplar_and_source(lang_scr)
  if exemplar is not None:
    sample_infos.append((rtl, sample_text_from_exemplar(exemplar), 'none', src_key))

  _, script = lang_scr.split('-')
  src_key = 'und-' + script + '_chars'
  sample_text = get_sample_from_sample_file(src_key)
  if sample_text is not None:
    sample_infos.append((rtl, sample_text, 'none', src_key))

  exemplar, src_key = cldr_data.get_exemplar_and_source('und-' + script)
  if exemplar is not None:
    sample_infos.append((rtl, sample_text_from_exemplar(exemplar), 'none', src_key))

  if not sample_infos:
    print '!No sample info for %s' % lang_scr

  return sample_infos


def get_family_id_to_default_lang_scr(family_id_to_lang_scrs):
  """Return a mapping from family id to default lang tag, for families
  that have multiple lang tags.  This is based on likely subtags and
  the script of the family (Latn for LGC).
  """

  family_id_to_default_lang_scr = {}
  for family_id, lang_scrs in family_id_to_lang_scrs.iteritems():
    parts = family_id.split('-')
    if len(parts) == 1:
      # 'sans' or 'serif'
      script = 'Latn'
    else:
      script = parts[1].capitalize()
      if script == 'Aran':
        script = 'Arab'

    if script == 'Zsym' or lang_data.is_excluded_script(script):
      lang = 'und'
    else:
      lang = lang_data.script_to_default_lang(script)
    lang_scr = lang + '-' + script

    if lang_scr not in lang_scrs:
      print 'default lang_scr \'%s\' not listed for family %s %s' % (
          lang_scr, family_id, lang_scrs)

    family_id_to_default_lang_scr[family_id] = lang_scr
  return family_id_to_default_lang_scr


lat_long_data = {}

def read_lat_long_data():
  with open(path.join(LAT_LONG_DIR, 'countries.csv')) as lat_long_file:
    for row in csv.reader(lat_long_file):
      region, latitude, longitude, _ = row
      if region == 'country':
          continue  # Skip the header
      if not latitude:
          continue  # Empty latitude
      latitude = float(latitude)
      longitude = float(longitude)
      lat_long_data[region] = (latitude, longitude)

  # From the English Wikipedia and The World Factbook at
  # https://www.cia.gov/library/publications/the-world-factbook/fields/2011.html
  lat_long_data.update({
      'AC': (-7-56/60, -14-22/60),  # Ascension Island
      'AX': (60+7/60, 19+54/60),  # Åland Islands
      'BL': (17+54/60, -62-50/60),  # Saint Barthélemy
      'BQ': (12+11/60, -68-14/60),  # Caribbean Netherlands
      'CP': (10+18/60, -109-13/60),  # Clipperton Island
      'CW': (12+11/60, -69),  # Curaçao
      'DG': (7+18/60+48/3600, 72+24/60+40/3600),  # Diego Garcia
       # Ceuta and Melilla, using Ceuta
      'EA': (35+53/60+18/3600, -5-18/60-56/3600),
      'IC': (28.1, -15.4),  # Canary Islands
      'MF': (18+4/60+31/3600, -63-3/60-36/3600),  # Saint Martin
      'SS': (8, 30),  # South Sudan
      'SX': (18+3/60, -63-3/60),  # Sint Maarten
      'TA': (-37-7/60, -12-17/60),  # Tristan da Cunha
       # U.S. Outlying Islands, using Johnston Atoll
      'UM': (16+45/60, -169-31/60),
  })


def get_region_lat_lng_data(regions):
  if not lat_long_data:
    read_lat_long_data()
  return lat_long_data


def get_css_generic_family(family):
    if family in {'Noto Naskh', 'Noto Serif', 'Tinos'}:
        return 'serif'
    if family in {'Arimo', 'Noto Kufi', 'Noto Sans'}:
        return 'sans-serif'
    if family == 'Cousine':
        return 'monospace'
    return None


CSS_WEIGHT_MAPPING = {
    'Thin': 250,
    'Light': 300,
    'DemiLight': 350,
    'Regular': 400,
    'Medium': 500,
    'Bold': 700,
    'Black': 900,
}

def css_weight(weight_string):
    return CSS_WEIGHT_MAPPING[weight_string]


CSS_WEIGHT_TO_STRING = {s:w for w, s in CSS_WEIGHT_MAPPING.items()}

def css_weight_to_string(weight):
    return CSS_WEIGHT_TO_STRING[weight]


def css_style(style_value):
    if style_value is None:
        return 'normal'
    else:
        assert style_value == 'Italic'
        return 'italic'


class WebGen(object):

  def __init__(self, target, clean, pretty_json, no_zips=False, no_images=False, no_css=False, no_data=False, no_build=False):
    self.target = target
    self.clean = clean
    self.pretty_json = pretty_json
    self.no_zips = no_zips
    self.no_images = no_images
    self.no_css = no_css
    self.no_data = no_data
    self.no_build = no_build or (no_zips and no_images and no_css and no_data)

    self.pkgs = path.join(target, 'pkgs')
    self.fonts = path.join(target, 'fonts')
    self.css = path.join(target, 'css')
    self.samples = path.join(target, 'samples')
    self.data = path.join(target, 'data')

  def clean_target_dir(self):
    if path.exists(self.target):
        print 'Removing the old website directory from %s...' % self.target
        shutil.rmtree(self.target)

  def write_json(self, obj, name):
    filepath = path.join(self.data, name + '.json')
    with codecs.open(filepath, 'w', encoding='UTF-8') as f:
      json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))

    if self.pretty_json:
      filepath = path.join(self.data, 'pretty', name + '-pretty.json')
      with codecs.open(filepath, 'w', encoding='UTF-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ': '),
                       indent=4)

  def ensure_target_dirs_exist(self):
    def mkdirs(p):
      if not path.exists(p):
        os.makedirs(p)
    mkdirs(self.target)
    mkdirs(self.pkgs)
    mkdirs(self.css)
    mkdirs(self.fonts)
    mkdirs(self.samples)
    mkdirs(self.data)
    if self.pretty_json:
      mkdirs(path.join(self.data, 'pretty'))

  def create_zip(self, name, fonts):
    zipname = name + '.zip'
    zippath = path.join(self.pkgs, zipname)
    if path.isfile(zippath):
      print('Assuming %s is valid.' % zipname)
    else:
      pairs = []
      license_types = set()
      for font in fonts:
        pairs.append((font.filepath, path.basename(font.filepath)))
        license_types.add(font.license_type)
      if 'apache' in license_types:
        pairs.append((APACHE_LICENSE_LOC, 'LICENSE.txt'))
      if 'sil' in license_types:
        pairs.append((SIL_LICENSE_LOC, 'LICENSE_CJK.txt'))
      tool_utils.generate_zip_with_7za_from_filepairs(pairs, zippath)
      print 'Created zip %s' % zippath
    return os.stat(zippath).st_size

  def build_family_zips(self, key, family):
    zip_name = family.name.replace(' ', '')
    hinted_size = 0
    unhinted_size = 0
    if family.hinted_members:
      hinted_size = self.create_zip(zip_name + '-hinted', family.hinted_members)
    if family.unhinted_members:
      unhinted_size = self.create_zip(zip_name + '-unhinted', family.unhinted_members)
    return zip_name, hinted_size, unhinted_size

  def build_zips(self, families):
    zip_info = {}
    for key, family_data in families.iteritems():
      zip_info[key] = self.build_family_zips(key, family_data)
    return zip_info

  def build_universal_zips(self, families):
    hinted_fonts = []
    unhinted_fonts = []
    for family_data in families.values():
      hinted_fonts.extend(family_data.hinted_members or family_data.unhinted_members)
      unhinted_fonts.extend(family_data.unhinted_members or family_data.hinted_members)
    hinted_size = self.create_zip('Noto-hinted', hinted_fonts)
    unhinted_size = self.create_zip('Noto-unhinted', unhinted_fonts)
    return 'Noto', hinted_size, unhinted_size

  def copy_font(self, fontpath):
    basename = path.basename(fontpath)
    dst = path.join(self.fonts, basename)
    shutil.copy(fontpath, dst)
    return basename

  def build_family_css(self, key, family):
    fonts = family.hinted_members or family.unhinted_members
    css_name = key + '.css'
    csspath = path.join(self.css, css_name)
    font_size = 0
    with open(csspath, 'w') as css_file:
      for font in fonts:
        font_name = self.copy_font(font.filepath)
        font_size = max(font_size, os.stat(font.filepath).st_size)
        css_file.write(
          '@font-face {\n'
          '  font-family: "%s";\n'
          '  font-weight: %d;\n'
          '  font-style: %s;\n'
          '  src: url(../fonts/%s) format("truetype");\n'
          '}\n' % (
              family.name,
              css_weight(font.weight),
              css_style(font.slope),
              font_name)
          )
    return font_size

  def build_css(self, families):
    css_info = {}
    for key, family_data in families.iteritems():
      css_info[key] = self.build_family_css(key, family_data)
    return css_info

  def build_data_json(self, families, family_zip_info, universal_zip_info,
                      family_id_to_lang_scrs, family_id_to_regions,
                      lang_scr_to_family_ids, region_to_family_ids):

    data_obj = collections.OrderedDict()
    families_obj = collections.OrderedDict()
    # Sort families by English name, except 'Noto Sans' and 'Noto Serif' come first
    family_ids = [family_id for family_id in families if family_id != 'sans' and family_id != 'serif']
    family_ids = sorted(family_ids, key=lambda f: families[f].name)
    sorted_ids = ['sans', 'serif']
    sorted_ids.extend(family_ids)
    for k in sorted_ids:
      family = families[k]
      family_obj = {}
      family_obj['name'] = family.name

      name, hinted_size, unhinted_size = family_zip_info[k]
      pkg_obj = collections.OrderedDict()
      if hinted_size:
        pkg_obj['hinted'] = hinted_size
      if unhinted_size:
        pkg_obj['unhinted'] = unhinted_size
      family_obj['pkgSize'] = pkg_obj

      family_obj['fonts'] = len(family.hinted_members or family.unhinted_members)
      family_obj['langs'] = len(family_id_to_lang_scrs[k])
      family_obj['regions'] = len(family_id_to_regions[k])

      families_obj[k] = family_obj
    data_obj['family'] = families_obj

    data_obj['familyOrder'] = sorted_ids

    langs_obj = collections.OrderedDict()
    # Dont list 'und-' lang tags, these are for default samples and not listed in the UI
    lang_scrs = [l for l in lang_scr_to_family_ids if not l.startswith('und-')]

    # sort by english name
    lang_scrs = sorted(lang_scrs, key=lambda l: lang_data.lang_script_to_names(l)[0])
    for lang_scr in lang_scrs:
      lang_obj = collections.OrderedDict()
      names = lang_data.lang_script_to_names(lang_scr)
      english_name = names[0]
      lang_obj['name'] = english_name
      lang_obj['families'] = sorted(lang_scr_to_family_ids[lang])
      native_names = [n for n in names[1:] if n != english_name]
      if native_names:
        lang_obj['keywords'] = native_names
      langs_obj[lang] = lang_obj
    data_obj['lang'] = langs_obj

    regions_obj = collections.OrderedDict()
    for region in sorted(region_to_family_ids,
                         key=lambda r: cldr_data.get_english_region_name(r)):
      region_obj = collections.OrderedDict()
      region_obj['families'] = sorted(region_to_family_ids[region])
      region_obj['keywords'] = [cldr_data.get_english_region_name(region)]
      regions_obj[region] = region_obj
    data_obj['region'] = regions_obj

    pkg_obj = collections.OrderedDict()
    pkg_obj['hinted'] = universal_zip_info[1]
    pkg_obj['unhinted'] = universal_zip_info[2]
    data_obj['pkgSize'] = pkg_obj

    self.write_json(data_obj, 'data')

  def build_family_json(self, family_id, family, lang_scrs, regions, css_info,
                        default_lang_scr):
    family_obj = collections.OrderedDict()
    category = get_css_generic_family(family.name)
    if category:
      family_obj['category'] = category
    if lang_scrs:
       # maintain provided sort order
      family_obj['langs'] = lang_scrs
    if default_lang_scr:
      family_obj['defaultLang'] = default_lang_scr
    if regions:
      family_obj['regions'] = sorted(regions)
    if family.charset:
      family_obj['ranges'] = get_charset_info(family.charset)
    fonts_obj = []
    members = []
    members.extend(family.hinted_members or family.unhinted_members)
    members.sort(key=lambda f: str(css_weight(f.weight)) + '-' +
                                   ('b' if css_style(f.slope) == 'italic' else 'a'))
    for font in members:
      weight_style = collections.OrderedDict()
      weight_style['weight'] = css_weight(font.weight)
      weight_style['style'] = css_style(font.slope)
      fonts_obj.append(weight_style)
    family_obj['fonts'] = fonts_obj
    family_obj['fontSize'] = css_info
    self.write_json(family_obj, family_id)

  def build_families_json(self, families, family_id_to_lang_scrs,
                          family_id_to_default_lang_scr,
                          family_id_to_regions, family_css_info,
                          lang_sort_order):
    for k, v in families.iteritems():
      lang_scrs = list(family_id_to_lang_scrs[k])
      lang_scrs.sort(key=lambda l: lang_sort_order[l])
      default_lang_scr = family_id_to_default_lang_scr[k]
      regions = family_id_to_regions[k]
      css_info = family_css_info[k]
      self.build_family_json(k, v, lang_scrs, regions, css_info, default_lang_scr)

  def build_misc_json(self, lang_scr_to_family_id_to_sample_key, sample_key_to_info,
                      region_data):
    lang_info = sample_data
    meta_obj = collections.OrderedDict()

    langs_obj = collections.OrderedDict()
    for lang in sorted(lang_info):
      rtl, sample, attrib, sample_key = lang_info[lang]
      lang_obj = collections.OrderedDict()
      lang_obj['sample'] = sample
      lang_obj['attrib'] = attrib
      if rtl:
        lang_obj['rtl'] = rtl
      langs_obj[lang] = lang_obj
    meta_obj['lang'] = langs_obj

    # don't need much accuracy for our map UI use case
    def trim_decimals(num):
      return float('%1.2f' % num)

    regions_obj = collections.OrderedDict()
    for region in sorted(region_data):
      lat, lng = region_data[region]
      lat = trim_decimals(lat)
      lng = trim_decimals(lng)
      region_obj = collections.OrderedDict()
      region_obj['lat'] = lat
      region_obj['lng'] = lng
      regions_obj[region] = region_obj

    meta_obj['region'] = regions_obj

    self.write_json(meta_obj, 'meta')

  def build_family_images(self, family, lang_scr, is_rtl, sample_text, attrib):
    family_id = family.rep_member.key
    is_cjk = family.rep_member.is_cjk
    for font in family.hinted_members or family.unhinted_members:
      weight = css_weight(font.weight)
      style = css_style(font.slope)
      image_file_name = '%s_%s_%d_%s.svg' % (family_id, lang_scr, weight, style)
      if is_cjk:
        family_suffix = ' ' + font.weight
      else:
        family_suffix = ''
      image_location = path.join(self.samples, image_file_name)
      if path.isfile(image_location):
        # Don't rebuild images when continuing.
        print "Continue: assuming image file '%s' is valid." % image_location
        continue
      print 'create %s' % image_file_name
      create_image.create_svg(
          sample_text,
          image_location,
          family=family.name + family_suffix,
          language=lang_scr,
          rtl=is_rtl,
          width=685,
          # text is coming out bigger than we expect, perhaps this is why?
          font_size=int(20 * (72.0/96.0)),
          line_spacing=int(32 * (72.0/96.0)),
          weight=weight,
          style=style)

  def build_images(self, families, family_id_to_lang_scrs, family_id_to_default_lang_scr,
                   lang_scr_to_family_id_to_sample_key, sample_key_to_info):
    for family_id, family in families.iteritems():
      print 'Generating images for %s...' % family.name
      default_lang = family_id_to_default_lang_scr[family_id]
      lang_scrs = family_id_to_lang_scrs[family_id]
      if not default_lang in lang_scrs:
        print 'build extra default image for lang %s' % default_lang
        lang_scrs = lang_scrs[:]
        lang_scrs.append(default_lang)
      for lang_scr in lang_scrs:
        sample_key = lang_scr_to_family_id_to_sample_key[lang_scr][family_id]
        is_rtl, sample_text, attrib, sample_key = sample_key_to_info[sample_key]
        self.build_family_images(family, lang_scr, is_rtl, sample_text, attrib)

  def build_ttc_zips(self):
    """Generate zipped versions of the ttc files and put in pkgs directory."""

    # The font family code skips the ttc files, but we want them in the
    # package directory. Instead of mucking with the family code to add the ttcs
    # and then exclude them from the other handling, we'll just handle them
    # separately.
    # For now at least, the only .ttc fonts are the CJK fonts

    filenames = [path.basename(f) for f in os.listdir(CJK_DIR) if f.endswith('.ttc')]
    for filename in filenames:
      zip_basename = filename + '.zip'
      zip_path = path.join(self.pkgs, zip_basename)
      if path.isfile(zip_path):
          print("Continue: assuming built %s is valid." % zip_basename)
          continue
      oldsize = os.stat(path.join(CJK_DIR, filename)).st_size
      pairs = [(path.join(CJK_DIR, filename), filename),
               (SIL_LICENSE_LOC, 'LICENSE_CJK.txt')]
      tool_utils.generate_zip_with_7za_from_filepairs(pairs, zip_path)
      newsize = os.stat(zip_path).st_size
      print "Wrote " + zip_path
      print 'Compressed from {0:,}B to {1:,}B.'.format(oldsize, newsize)

  def generate(self):
    if self.clean:
      self.clean_target_dir()

    if not self.no_build:
      self.ensure_target_dirs_exist()

    # debug/print
    # ['families', 'script_to_family_ids', 'used_lang_data',
    #  'family_id_to_lang_scrs', 'family_id_to_default_lang_scr']
    debug = frozenset([])

    fonts = noto_fonts.get_noto_fonts()
    families = noto_fonts.get_families(fonts)

    if 'families' in debug:
      print '\nfamilies'
      for family_id, family in sorted(families.iteritems()):
        print family_id, family.rep_member.script

    script_to_family_ids = get_script_to_family_ids(families)
    if 'script_to_family_ids' in debug:
      print '\nscript to family ids'
      for script, family_ids in sorted(script_to_family_ids.iteritems()):
        print script, family_ids

    lang_scr_to_sample_infos = {}
    for lang_scr in lang_data.lang_scripts():
      # Note lang_scripts only includes those with english names, so we
      # don't need to filter for that.
      lang, script = lang_scr.split('-')
      if not script in script_to_family_ids:
        print 'no family supports script in %s' % lang_scr
        continue

      rtl = cldr_data.is_rtl(lang_scr)
      sample_infos = get_sample_infos(lang_scr, rtl)
      if not sample_infos:
        continue

      lang_scr_to_sample_infos[lang_scr] = sample_infos

    if 'lang_scr_to_sample_infos' in debug:
      print '\nlang+script to sample infos'
      for lang_scr, info_list in sorted(lang_scr_to_sample_infos.iteritems()):
        for info in info_list:
          print '%s: %s' % (lang_scr, info)

    family_id_to_lang_scrs = get_family_id_to_lang_scrs(
        lang_scr_to_sample_infos.keys(), script_to_family_ids)

    family_id_to_lang_scr_to_sample_key, sample_key_to_info = get_family_id_to_lang_scr_to_sample_key(
        family_id_to_lang_scrs, families, lang_scr_to_sample_infos)

    region_to_family_ids = get_region_to_family_ids(script_to_family_ids)

    family_id_to_regions = get_family_id_to_regions(region_to_family_ids, families)

    family_id_to_default_lang_scr = get_family_id_to_default_lang_scr(
        family_id_to_lang_scrs)
    if 'family_id_to_default_lang_scr' in debug:
      print '\nfamily id to default lang scr'
      for family_id, lang_scr in family_id_to_default_lang_scr.iteritems():
        print family_id, lang_scr

    region_data = get_region_lat_lng_data(region_to_family_ids.keys())

    lang_scrs = get_supported_lang_scrs(family_id_to_lang_scr_to_sample_key)
    lang_scr_sort_order = get_lang_scr_sort_order(lang_scrs)

    if self.no_build:
      print 'skipping build output'
      return

    # build outputs
    if self.no_zips:
      print 'skipping zip output'
    else:
      family_zip_info = self.build_zips(families)
      universal_zip_info = self.build_universal_zips(families)

      # build outputs not used by the json but linked to from the web page
      self.build_ttc_zips()

    if self.no_css:
      print 'skipping css output'
    else:
      family_css_info = self.build_css(families)

    if self.no_data or self.no_zips or self.no_css:
      reason = '' if self.no_data else 'no zips' if self.no_zips else 'no css'
      print 'skipping data output%s' % reason
    else:
      self.build_data_json(families, family_zip_info, universal_zip_info,
                           family_id_to_lang_scrs, family_id_to_regions,
                           lang_scr_to_family_ids, region_to_family_ids)

      self.build_families_json(families, family_id_to_lang_scrs,
                               family_id_to_default_lang_scr,
                               family_id_to_regions, family_css_info,
                               lang_scr_sort_order)

      self.build_misc_json(lang_scr_to_family_id_to_sample_key,
                           sample_key_to_info, region_data)

    if self.no_images:
      print 'skipping image output'
    else:
      self.build_images(families, family_id_to_lang_scrs,
                        family_id_to_default_lang_scr, lang_tag_family_id_to_sample_key,
                        sample_key_to_info)



def main():
    """Outputs data files for the noto website."""

    default_target = '/tmp/website2'

    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--clean',  help='clean target directory',
                        action='store_true')
    parser.add_argument('-t', '--target', help='target dir, default %s' %
                        default_target, default=default_target, metavar='dir')
    parser.add_argument('-pj', '--pretty_json', help='generate additional pretty json',
                        action='store_true')
    parser.add_argument('-nz', '--no_zips', help='skip zip generation', action='store_true')
    parser.add_argument('-ni', '--no_images', help='skip image generation', action='store_true')
    parser.add_argument('-nd', '--no_data', help='skip data generation', action='store_true')
    parser.add_argument('-nc', '--no_css', help='skip css generation', action='store_true')
    parser.add_argument('-n', '--no_build', help='skip build of zip, image, data, and css',
                        action='store_true')
    args = parser.parse_args();

    webgen = WebGen(args.target, args.clean, args.pretty_json,
                    no_zips=args.no_zips, no_images=args.no_images, no_css=args.no_css,
                    no_data=args.no_data, no_build=args.no_build)
    webgen.generate()


if __name__ == '__main__':
    locale.setlocale(locale.LC_COLLATE, 'en_US.UTF-8')
    main()
