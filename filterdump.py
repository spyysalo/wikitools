#!/usr/bin/env python3

import sys
import os
import bz2
import logging
import xml.etree.ElementTree as ET

import mwparserfromhell

from functools import wraps
from time import time
from argparse import ArgumentParser


logging.basicConfig()
logger = logging.getLogger()


DEFAULT_NS_URI = 'http://www.mediawiki.org/xml/export-0.10/'


# Filter templates with names starting with any of these strings
FILTERED_TEMPLATE_PREFIXES = {
    'Infobox',
    'Navbox',
    'Tietolaatikko',    # Infobox in Finnish Wikipedia
}


# see https://www.mediawiki.org/wiki/Help:Magic_words
MAGIC_WORDS = {
    'CURRENTYEAR',
    'CURRENTMONTH',
    'CURRENTMONTH1',
    'CURRENTMONTHNAME',
    'CURRENTMONTHNAMEGEN',
    'CURRENTMONTHABBREV',
    'CURRENTDAY',
    'CURRENTDAY2',
    'CURRENTDOW',
    'CURRENTDAYNAME',
    'CURRENTTIME',
    'CURRENTHOUR',
    'CURRENTWEEK',
    'CURRENTTIMESTAMP',
    'LOCALYEAR',
    'LOCALMONTH',
    'LOCALMONTH1',
    'LOCALMONTHNAME',
    'LOCALMONTHNAMEGEN',
    'LOCALMONTHABBREV',
    'LOCALDAY',
    'LOCALDAY2',
    'LOCALDOW',
    'LOCALDAYNAME',
    'LOCALTIME',
    'LOCALHOUR',
    'LOCALWEEK',
    'LOCALTIMESTAMP',
    'SITENAME',
    'SERVER',
    'SERVERNAME',
    'DIRMARK',
    'DIRECTIONMARK',
    'SCRIPTPATH',
    'STYLEPATH',
    'CURRENTVERSION',
    'CONTENTLANGUAGE',
    'CONTENTLANG',
    'PAGEID',
    'PAGELANGUAGE',
    'CASCADINGSOURCES',
    'REVISIONID',
    'REVISIONDAY',
    'REVISIONDAY2',
    'REVISIONMONTH',
    'REVISIONMONTH1',
    'REVISIONYEAR',
    'REVISIONTIMESTAMP',
    'REVISIONUSER',
    'REVISIONSIZE',
    'NUMBEROFPAGES',
    'NUMBEROFARTICLES',
    'NUMBEROFFILES',
    'NUMBEROFEDITS',
    'NUMBEROFVIEWS',
    'NUMBEROFUSERS',
    'NUMBEROFADMINS',
    'NUMBEROFACTIVEUSERS',
    'FULLPAGENAME',
    'PAGENAME',
    'BASEPAGENAME',
    'ROOTPAGENAME',
    'SUBPAGENAME',
    'ARTICLEPAGENAME',
    'SUBJECTPAGENAME',
    'TALKPAGENAME',
    'NAMESPACENUMBER',
    'NAMESPACE',
    'ARTICLESPACE',
    'SUBJECTSPACE',
    'TALKSPACE',
    'FULLPAGENAMEE',
    'PAGENAMEE',
    'BASEPAGENAMEE',
    'SUBPAGENAMEE',
    'SUBJECTPAGENAMEE',
    'ARTICLEPAGENAMEE',
    'TALKPAGENAMEE',
    'ROOTPAGENAMEE',
    'NAMESPACEE',
    'SUBJECTSPACEE',
    'ARTICLESPACEE',
    'TALKSPACEE',
    'SHORTDESC',
    '!',    # see https://en.wikipedia.org/wiki/Template:!
}


def argparser():
    ap = ArgumentParser()
    ap.add_argument('--templates', help='File for saving/loading templates')
    ap.add_argument('--debug', default=False, action='store_true')
    ap.add_argument('dump')
    return ap


def timed(f, out=sys.stderr):
    @wraps(f)
    def wrapper(*args, **kwargs):
        start = time()
        result = f(*args, **kwargs)
        delta = time() - start
        logger.info('{} completed in {:.1f} sec'.format(f.__name__, delta))
        return result
    return wrapper


class Devnull:
    def read(self, *_):
        return ''

    def write(self, *_):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def zopen(fn):
    if fn.endswith('.bz2'):
        return bz2.open(fn)
    else:
        return open(fn)


def nopen(fn, *args):
    if fn is None:
        return Devnull()
    else:
        return open(fn, *args)


def register_namespace(prefix, uri):
    if prefix == '' and uri != DEFAULT_NS_URI:
        logger.warning(f'unexpected default namespace "{uri}"')
    ET.register_namespace(prefix, uri)
    register_namespace.namespaces[prefix] = uri
register_namespace.namespaces = {}


def tag(name, namespace=''):
    return f'{{{register_namespace.namespaces[namespace]}}}{name}'


class SiteInfo:
    def __init__(self, dbname, urlbase, namespaces):
        self.dbname = dbname
        self.urlbase = urlbase
        self.namespaces = namespaces

    def template_namespace_name(self):
        # "10" is the mediawiki magic key value for the template namespace
        return self.namespaces['10']['name']

    def module_namespace_name(self):
        # "828" is the mediawiki magic key value for the module namespace
        return self.namespaces['828']['name']

    def __str__(self):
        return f'<SiteInfo dbname={self.dbname} base={self.urlbase}>'

    @classmethod
    def from_xml(cls, element):
        dbname = first_child(element, 'dbname').text
        urlbase = first_child(element, 'base').text
        namespaces = {}
        for e in first_child(element, 'namespaces'):
            assert e.tag == tag('namespace')
            key, case, name = e.attrib['key'], e.attrib['case'], e.text
            namespaces[key] = {
                'case': case,
                'name': name,
            }
        return cls(dbname, urlbase, namespaces)


class Page:
    def __init__(self, title, redirect, text):
        self.title = title
        self.redirect = redirect
        self.text = text
        self._wikicode = None

    def __str__(self):
        return f'<Page title={self.title} redirect={self.redirect}>'

    @property
    def wikicode(self):
        if self._wikicode is None:
            self._wikicode = mwparserfromhell.parse(self.text)
        return self._wikicode

    @classmethod
    def from_xml(cls, element):
        title = first_child(element, 'title').text
        redirect = first_child(element, 'redirect', allow_missing=True)
        if redirect is not None:
            redirect = redirect.attrib['title']
        revision = first_child(element, 'revision')
        text = first_child(revision, 'text').text
        return cls(title, redirect, text)


def first_child(element, tagname, namespace='', allow_missing=False):
    tagname = tag(tagname, namespace)
    e = element.find(tagname)
    if e is not None:
        return e
    elif allow_missing:
        return None
    else:
        raise ValueError(f'failed to find {tagname} in {element}')


def xmlstr(element):
    return ET.tostring(element, encoding='unicode')


@timed
def load_templates(source, target):
    print(f'<templates xmlns="{DEFAULT_NS_URI}">',
          file=target)
    siteinfo, templates = None, {}
    for event, e in ET.iterparse(source, events=('end', 'start-ns')):
        if event == 'start-ns':
            register_namespace(*e)
        elif event == 'end' and e.tag == tag('siteinfo'):
            print('  ' + xmlstr(e), file=target)
            siteinfo = SiteInfo.from_xml(e)
        elif event == 'end' and e.tag == tag('page'):
            title = first_child(e, 'title').text
            template_ns = f'{siteinfo.template_namespace_name()}:'
            if title.startswith(template_ns):
                print('  ' + xmlstr(e), file=target)
                template_name = title[len(template_ns):]
                templates[template_name] = Page.from_xml(e)
            e.clear()    #  preserve memory
    print('</templates>', file=target)
    return siteinfo, templates


def is_magic_word(string):
    """Return True iff string is one of the MediaWiki "magic words"."""
    return string in MAGIC_WORDS


def is_parser_function(string):
    """Return True iff string is a MediaWiki parser function."""
    # see https://www.mediawiki.org/wiki/Help:Extension:ParserFunctions
    return string.startswith('#')    # close enough for our needs


def normalize_template_name(name):
    name = str(name).strip()
    return name[0].upper() + name[1:]


def is_filtered_by_name(siteinfo, title):
    for prefix in FILTERED_TEMPLATE_PREFIXES:
        if title.startswith(prefix):
            return True
    return False


def _is_filtered(siteinfo, title, templates):
    title = normalize_template_name(title)
    if is_filtered_by_name(siteinfo, title):
        logger.debug(f'filtered by name: {title}')
        return True
    elif is_magic_word(title) or is_parser_function(title):
        logger.debug(f'magic word or function: {title}')
        return False
    elif title not in templates:
        logger.error(f'missing template {title}')
        return False
    else:
        for t in templates[title].wikicode.filter_templates():
            tname = normalize_template_name(t.name)
            if is_filtered(siteinfo, tname, templates):
                logger.debug(f'filtered recursively: {title}')
                return True
            logger.debug(f'not filtered: {title}')
            return False


def is_filtered(siteinfo, title, templates):
    key = title
    if key not in is_filtered.cache:
        if key in is_filtered.active:
            # Protect against reference loops
            logger.warning(f'not recursing into {title}')
            return None
        is_filtered.active.add(key)
        is_filtered.cache[key] = _is_filtered(siteinfo, title, templates)
        is_filtered.active.remove(key)
    return is_filtered.cache[key]
is_filtered.cache = {}
is_filtered.active = set()


def remove_filtered_templates(siteinfo, wikicode, templates):
    for t in wikicode.filter_templates():
        tname = normalize_template_name(t.name)
        if is_filtered(siteinfo, tname, templates):
            try:
                wikicode.remove(t)
            except ValueError as e:
                logger.error(f'failed to remove {tname}: {e}')


def write_dump_header(elem, out):
    copy = ET.Element(elem.tag, elem.attrib)
    copy.text = ' '    # must be nonempty
    string = ET.tostring(copy, encoding='unicode')
    string = string.replace('</mediawiki>', '').strip()
    print(string, file=out)


@timed
def filter_dump(source, target, templates):
    siteinfo = None
    for event, e in ET.iterparse(source, events=('start', 'end', 'start-ns')):
        if event == 'start-ns':
            register_namespace(*e)
        elif event == 'start' and e.tag == tag('mediawiki'):
            write_dump_header(e, target)
        elif event == 'end' and e.tag == tag('siteinfo'):
            siteinfo = SiteInfo.from_xml(e)
            print('  ' + xmlstr(e), end='', file=target)
        elif event == 'end' and e.tag == tag('page'):
            page = Page.from_xml(e)
            wikicode = mwparserfromhell.parse(page.text)
            remove_filtered_templates(siteinfo, wikicode, templates)
            revision = first_child(e, 'revision')
            textelem = first_child(revision, 'text')
            textelem.text = str(wikicode)
            # TODO fix attrib "bytes" in textelem
            print(xmlstr(e), file=target)
            e.clear()    #  preserve memory
    print('</mediawiki>', file=target)


def main(argv):
    args = argparser().parse_args(argv[1:])

    if args.debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    siteinfo, templates = None, None
    if args.templates is not None and os.path.exists(args.templates):
        try:
            with zopen(args.templates) as source:
                siteinfo, templates = load_templates(source, Devnull())
        except Exception as e:
            logging.error(f'loading templates from {args.templates}: {e}')

    if templates is None:
        with zopen(args.dump) as source:
            with nopen(args.templates, 'wt') as target:
                siteinfo, templates = load_templates(source, target)

    with zopen(args.dump) as source:
        filter_dump(source, sys.stdout, templates)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
