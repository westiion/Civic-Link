
import re

def tokenize(text):
    tokens = []
    for word in re.findall(r"[가-힣]+|[a-z0-9]+", text.lower()):
        tokens.append("w:" + word)
        if re.fullmatch(r"[가-힣]+", word):
            tokens.extend("b:" + word[i:i + 2] for i in range(len(word) - 1))
    return tokens

