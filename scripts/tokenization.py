
import re
import unicodedata
from functools import lru_cache
from kiwipiepy import Kiwi

TOKENIZER_NAME = 'kiwi-content+surface-v1'
CONTENT_TAGS = ('NN', 'NR', 'NP', 'VV', 'VA', 'MAG', 'XR', 'SL', 'SN')
ARTICLE = re.compile(r'제\s*(\d+)\s*조(?:\s*의\s*(\d+))?')
SURFACE = re.compile(r'[가-힣]{2,}|[a-z][a-z0-9_-]*|\d+', re.IGNORECASE)


@lru_cache(maxsize=1)
def analyzer():
    return Kiwi()


def normalize(text):
    if not isinstance(text, str):
        raise TypeError('text must be a string')
    return unicodedata.normalize('NFC', text).lower()


def tokenize(text):
    value = normalize(text)
    tokens = ['l:제'+m.group(1)+'조'+(('의'+m.group(2)) if m.group(2) else '') for m in ARTICLE.finditer(value)]
    tokens.extend('s:'+m.group() for m in SURFACE.finditer(value))
    for token in analyzer().tokenize(value):
        form = token.form.lower()
        if token.tag.startswith(CONTENT_TAGS) and (len(form) >= 2 or token.tag.startswith(('SL', 'SN'))):
            tokens.append('m:'+form)
    return tokens


def token_set(text):
    return set(tokenize(text))

