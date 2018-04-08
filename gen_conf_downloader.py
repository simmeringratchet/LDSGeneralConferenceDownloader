"""
Script to download LDS General Conference MP3s, creating playlists for each conference, speaker and topic
"""

import html as html_tools
import json
import os
import re
import shutil
import sys
from urllib.parse import unquote_plus
from urllib.parse import quote_plus
import urllib.request
import zlib
from collections import defaultdict, namedtuple
from mutagen.mp3 import MP3
from tqdm import tqdm

Season = namedtuple('Season', 'link year month title')
Session = namedtuple('Session', 'html title season')
Talk = namedtuple('Talk', 'link speaker title session')

speakers_num = defaultdict(int)
topics_num = defaultdict(int)
speakers_secs = defaultdict(int)
topics_secs = defaultdict(int)

CACHE_DIR = 'cache/'
CONFERENCES_DIR = 0
TOPICS_DIR = 2
SPEAKERS_DIR = 1
AUDIO_DUR = 'MP3'
PLAYLIST_FILE_EXT = 'm3u'

LDS_ORG_URL = 'https://www.lds.org'
ALL_CONFERENCES_URL = f'{LDS_ORG_URL}/general-conference/conferences'

GET_SESSION_TITLE_REGEX = '<span class=\"section__header__title\">(.*?)</span>'
TALK_LINK_REGEX = '<source src=\"(.*?.mp3)\">'
TALK_TOPIC_REGEX = '<div class=\"drawerList tab\" data-title=\"(.*?)\">'
GET_TALK_LINKS_FROM_SESSION_SECTION_REGEX = '<div class=\"lumen-tile lumen-tile--horizontal lumen-tile--list\">.*?' \
                                            '<a href=\"(.*?)\" class=\"lumen-tile__link\">.*?<div ' \
                                            'class=\"lumen-tile__title\">.*?<div>(.*?)</div>.*?<div ' \
                                            'class=\"lumen-tile__content\">(.*?)</div>'
GET_LANGS_REGEX = 'data-lang=\".*?\" data-clang=\"(.*?)\">(.*?)</a>'
GET_CONFERENCE_SEASONS_REGEX = '<a href=\"(/general-conference/(\d{4})/(\d{2})\?lang&#x3D;\w{3})\" ' \
                               'class=\"year-line__link\">(.*?)</a>'
GET_SECTION_TERMS_REGEX = '<a class=\"link trigger trigger.*?\" data-target-watch=\"#toggled.*?\" id=\"trigger.*?\">' \
                          '(.*?)<span'

SESSION_SPLITTER = 'section tile-wrapper layout--3 lumen-layout__item'


def get_all_conferences_seasons(args):
    all_seasons_html = get(args, f'{ALL_CONFERENCES_URL}?lang={args.lang}')
    playlist_dirs = re.findall(GET_SECTION_TERMS_REGEX, all_seasons_html, re.S)
    remove_generated_files(args, playlist_dirs)

    all_season_details = re.findall(GET_CONFERENCE_SEASONS_REGEX, all_seasons_html, re.S)
    all_season_details = get_unique_sorted_list(all_season_details)
    seasons = [Season(season_detail[0], int(season_detail[1]), int(season_detail[2]), season_detail[3].strip())
               for season_detail in all_season_details]

    def in_range(start, end, value):
        return start <= value <= end

    seasons = [season for season in seasons if in_range(args.start, args.end, season.year)]

    with tqdm(total=len(seasons)) as progress_bar:
        for season in seasons:
            progress_bar.set_description_str(season.title, refresh=True)
            get_conference_season(args, playlist_dirs, season)
            progress_bar.update(1)

    add_counts_to_playlists(args, playlist_dirs)

    if not args.nocleanup:
        remove_cached_files(args)


def get_conference_season(args, playlist_dirs, season):
    season_html = get(args, f'{LDS_ORG_URL}{decode(season.link)}')
    session_htmls = season_html.split(SESSION_SPLITTER)

    sessions = list()
    for session_html in session_htmls:
        session_title_results = re.findall(GET_SESSION_TITLE_REGEX, session_html)
        if session_title_results:
            sessions.append(Session(session_html, session_title_results[0], season))

    with tqdm(total=len(sessions)) as progress_bar:
        for session in sessions:
            progress_bar.set_description_str(session.title, refresh=True)
            get_session(args, playlist_dirs, session)
            progress_bar.update(1)


def get_session(args, playlist_dirs, session):
    talk_summaries = get_talk_summary_details(session.html)
    talks = [Talk(decode(talk[0]), talk[2], talk[1], session) for talk in talk_summaries]

    with tqdm(total=len(talks)) as progress_bar:
        for talk in talks:
            progress_bar.set_description_str(talk.title, refresh=True)
            get_talk(args, playlist_dirs, talk)
            progress_bar.update(1)


def get_talk(args, playlist_dirs, talk):
    talk_html = get(args, f'{LDS_ORG_URL}{talk.link}')

    mp3_link_result = re.findall(TALK_LINK_REGEX, talk_html)
    if not mp3_link_result:
        return
    link_mp3 = mp3_link_result[0]

    topics = re.findall(TALK_TOPIC_REGEX, talk_html)
    topics = [to_camel_case(topic) for topic in topics]

    filename_mp3 = f'{AUDIO_DUR}/{talk.session.season.year}/{talk.session.season.month}/{talk.session.title}/' \
                   f'{talk.title} ({talk.speaker}).mp3'
    output_mp3_filepath = get_mp3(args, link_mp3, filename_mp3)
    duration = int(MP3(output_mp3_filepath).info.length)

    update_playlists(args, playlist_dirs, talk, filename_mp3, topics, duration)
    increment_counts(talk.speaker, topics, duration)


def get_mp3_filepath(year, month_text, session_lable_text, title_text, name_text):
    return f'mp3/{year}/{month_text}/{session_lable_text}/' \
           f'{year} {month_text}, {session_lable_text}, {title_text} ({name_text}).mp3'


def get_mp3(args, link_mp3, filename_mp3):
    mp3_output_filename = f'{get_output_dir(args)}/{filename_mp3}'
    get_mp3_file(link_mp3, mp3_output_filename)
    return mp3_output_filename


def update_playlists(args, playlist_dirs, talk, filename_mp3, topics, duration):
    session_playlist_filename = \
        f'{get_output_dir(args)}/{playlist_dirs[CONFERENCES_DIR]}/{talk.session.season.year}/' \
        f'{talk.session.season.month}/{talk.session.title}.{PLAYLIST_FILE_EXT}'
    append_to_playlist(session_playlist_filename, f'../../../{filename_mp3}', f'{talk.title} ({talk.speaker})',
                       duration)

    speaker_playlist_filename = f'{get_output_dir(args)}/{playlist_dirs[SPEAKERS_DIR]}/{talk.speaker}.' \
                                f'{PLAYLIST_FILE_EXT}'
    append_to_playlist(speaker_playlist_filename, f'../{filename_mp3}', f'{talk.title} ({talk.speaker})', duration)

    for topic in topics:
        topic_playlist_filename = f'{get_output_dir(args)}/{playlist_dirs[TOPICS_DIR]}/{to_camel_case(topic)}.' \
                                  f'{PLAYLIST_FILE_EXT}'
        append_to_playlist(topic_playlist_filename, f'../{filename_mp3}', f'{talk.title} ({talk.speaker})', duration)


def increment_counts(name_text, talk_topics, duration):
    speakers_num[name_text] += 1
    speakers_secs[name_text] += int(duration)
    for topic in talk_topics:
        topics_num[topic] += 1
        topics_secs[topic] += int(duration)


def remove_generated_files(args, playlist_dirs):
    shutil.rmtree(f'{get_output_dir(args)}/{playlist_dirs[CONFERENCES_DIR]}', ignore_errors=True)
    shutil.rmtree(f'{get_output_dir(args)}/{playlist_dirs[SPEAKERS_DIR]}', ignore_errors=True)
    shutil.rmtree(f'{get_output_dir(args)}/{playlist_dirs[TOPICS_DIR]}', ignore_errors=True)


def remove_cached_files(args):
    shutil.rmtree(f'{CACHE_DIR}{args.lang}', ignore_errors=True)


def add_counts_to_playlists(args, playlist_dirs):
    if speakers_num:
        speaker_playlist_dir = f'{get_output_dir(args)}/{playlist_dirs[SPEAKERS_DIR]}'
        speaker_playlists_files = os.listdir(speaker_playlist_dir)
        for speaker_playlist_file in speaker_playlists_files:
            speaker = speaker_playlist_file[:-4]
            count = speakers_num[speaker]
            duration_secs = speakers_secs[speaker]
            duration_text = get_duration_text(duration_secs)
            orig = f'{speaker_playlist_dir}/{speaker_playlist_file}'
            updated = f'{speaker_playlist_dir}/{speaker}({count}, {duration_text}).m3u'
            os.rename(orig, updated)

    if topics_num:
        topic_playlist_dir = f'{get_output_dir(args)}/{playlist_dirs[TOPICS_DIR]}'
        topic_playlists_files = os.listdir(topic_playlist_dir)
        for topic_playlist_file in topic_playlists_files:
            topic = topic_playlist_file[:-4]
            count = topics_num[topic]
            duration_secs = topics_secs[topic]
            duration_text = get_duration_text(duration_secs)
            orig = f'{topic_playlist_dir}/{topic_playlist_file}'
            updated = f'{topic_playlist_dir}/{topic}({count}, {duration_text}).m3u'
            os.rename(orig, updated)


def to_camel_case(text):
    return ''.join(x for x in text.title())


def get_unique_sorted_list(collection):
    collection = list(set(collection))
    collection.sort()
    collection.reverse()
    return collection


def get_duration_text(duration_secs):
    mins = int((duration_secs / 60) % 60)
    hours = int((duration_secs / (60 * 60)) % 24)
    days = int((duration_secs / (60 * 60 * 24)) % 7)
    weeks = int((duration_secs / (60 * 60 * 24 * 7)))

    text = ''
    if weeks:
        text = f'{weeks}w'
    if days:
        text += f'{days}d'
    if hours:
        text += f'{hours}h'
    if mins:
        text += f'{mins}m'

    return text


def get_talk_summary_details(session_html):
    return re.findall(GET_TALK_LINKS_FROM_SESSION_SECTION_REGEX, session_html, re.S)


def get_html_results(args, url, regexpr, flags=0):
    return re.findall(regexpr, get(args, url), flags=flags)


def get(args, url):
    url = html_tools.unescape(url)
    if args.verbose:
        print(url)
    req = urllib.request.Request(url)
    add_headers(req)

    cached = get_from_cache(args, url)
    if cached:
        return cached

    try:
        with urllib.request.urlopen(req) as response:
            data = response.read()
            decompressed_data = zlib.decompress(data, 16 + zlib.MAX_WBITS)
            html = decompressed_data.decode("utf-8")
            add_to_cache(args, html, url)
            return html
    except Exception as ex:
        sys.stderr.write(f'Problem with http request ({url}: {ex}')
        return ''


def get_mp3_file(url, file_path):
    file_data = read_mp3_from_disk(file_path)
    if file_data:
        return file_data

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = response.read()
            write_mp3_to_disk(data, file_path)
            return data
    except Exception as err:
        print(err)


def decode(text):
    return unquote_plus(text)


def add_headers(request):
    with open('conference_headers.json', 'r') as f:
        headers = json.load(f)

    for key in headers:
        request.add_header(key, headers[key])


def get_from_cache(args, url):
    url = html_tools.unescape(url)
    url = quote_plus(url)

    path = get_cache_filename(args, url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.isfile(path):
        with open(path, 'r') as f:
            return f.read()
    return None


def add_to_cache(args, html, url):
    url = html_tools.unescape(url).encode()
    url = quote_plus(url)
    path = get_cache_filename(args, url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(html)


def get_cache_filename(args, url):
    return f'{CACHE_DIR}/{args.lang}/{url}'


def read_mp3_from_disk(file_path):
    if os.path.isfile(file_path):
        with open(file_path, 'rb') as f:
            return f.read()
    return None


def write_mp3_to_disk(data, filename):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "wb") as f:
        f.write(data)


def append_to_playlist(playlist_filename, mp3_filename, title, duration):
    os.makedirs(os.path.dirname(playlist_filename), exist_ok=True)

    data = f'#EXTINF:{int(duration)}, {title}\n{mp3_filename}\n\n'
    if not os.path.isfile(playlist_filename):
        data = '#EXTM3U\n\n' + data

    with open(playlist_filename, "a") as f:
        f.write(data)


def get_output_dir(args):
    return f'{args.dest}/{args.lang}'


def validate_args(args):
    langs_list = get_html_results(args, f'{LDS_ORG_URL}/languages', GET_LANGS_REGEX)
    lang_mapping = dict(langs_list)
    if args.lang not in lang_mapping:
        sys.stderr.write(f'The given language ({args.lang}) is not available. Please choose one of the following:\n')
        for code in lang_mapping:
            sys.stderr.write(f'\t{lang_mapping[code]} = {code}\n')
        sys.exit(1)

    if args.start > args.end:
        sys.stderr.write(f'The start year ({args.start}) cannot be after the end year ({args.end}) \n')
        sys.exit(1)


class dummy_tqdm:
    def __init__(self, total=None):
        self.total = total
        pass

    def __len__(self):
        return self.total

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def set_description_str(self, desc=None, refresh=True):
        pass

    def update(self, n=1):
        pass


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Download language specific LDS General Conference MP3s, '
                                                 'creating playlists for each conference, speaker and topic.')
    parser.add_argument('-lang', help='Language version to download. '
                                      'See https://www.lds.org/languages for full list.', default='eng')
    parser.add_argument('-start', type=int, help='First year to download. '
                                                 'Note: not all historic sessions are available in all languages',
                        default=1971)
    parser.add_argument('-end', type=int, help='Last year to download (defaults to present year).', default=2100)
    parser.add_argument('-dest', help='Destination folder to output files to.', default='./output')
    parser.add_argument('-nocleanup', help='Leaves temporary files after process completion.', action="store_true")
    parser.add_argument('-verbose', help='Provides detailed activity logging instead of progress bars.',
                        action="store_true")

    cli_args = parser.parse_args()

    validate_args(cli_args)

    if cli_args.verbose:
        tqdm = dummy_tqdm

    get_all_conferences_seasons(cli_args)

    sys.stdout.write('\n\n\n')
