"""Conservative value parsing: no implied anniversaries or ambiguous numeric dates."""
import re
from datetime import date, datetime


def renewal_date(value):
    value = value.strip()
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    cleaned = re.sub(r'(\d)(?:st|nd|rd|th)\b', r'\1', value)
    for pattern in ['%B %d, %Y','%b %d, %Y','%B %d %Y','%b %d %Y','%d %B %Y','%d %b %Y']:
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    raise ValueError('An explicit date including year is required')


def notice_days(value):
    value = value.strip().lower()
    if 'business' in value or not re.search(r'\bdays?\b', value):
        raise ValueError('Only calendar-day notice is supported')
    numbers = re.findall(r'\d+', value)
    if len(numbers) == 1:
        count = int(numbers[0])
    else:
        words = value.replace('-', ' ').split()
        numbers_by_word = dict(zip('one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), range(1,20)))
        numbers_by_word.update(dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20,100,10))))
        numeric = [numbers_by_word[w] for w in words if w in numbers_by_word]
        if not numeric or numbers or len(numeric)>2 or any(w not in numbers_by_word and w not in {'day','days','calendar'} for w in words):
            raise ValueError('Unambiguous notice duration required')
        if len(numeric)==2 and not (numeric[0]>=20 and numeric[0]%10==0 and numeric[1]<10):
            raise ValueError('Ambiguous written duration')
        count = sum(numeric)
    if not 1 <= count <= 365:
        raise ValueError('Notice period must be between 1 and 365 calendar days')
    return count
