"""Extraction preserves character offsets; uploaded documents are never executed."""
import io
import re
import zipfile


def extract(raw, name):
    suffix = name.lower().rsplit('.', 1)[-1]
    if suffix in ('txt', 'md'):
        return raw.decode('utf-8')
    if suffix == 'pdf':
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        if len(reader.pages) > 100:
            raise ValueError('Maximum 100 pages')
        result = '\n\n'.join(page.extract_text() or '' for page in reader.pages)
        if len(result.strip()) < 30:
            raise ValueError('Scanned PDF requires OCR; upload extracted text instead')
        return result
    if suffix == 'docx':
        from docx import Document
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(x.file_size for x in archive.infolist()) > 20_000_000:
                raise ValueError('Expanded document exceeds limit')
        doc = Document(io.BytesIO(raw))
        return '\n\n'.join([p.text for p in doc.paragraphs] +
                           [' | '.join(c.text for c in r.cells) for t in doc.tables for r in t.rows])
    raise ValueError('Supported files: TXT, MD, PDF and DOCX')


def chunks(text, strategy='clause', encoder=None):
    if strategy == 'semantic':
        if encoder is None:
            raise ValueError('Semantic chunking requires the neural retrieval profile')
        units = chunks(text, 'recursive')
        # Compare paragraph embeddings; keep original offsets, whitespace and order.
        vectors = encoder([c['text'] for c in units]) if units else []
        grouped = []
        for i, unit in enumerate(units):
            if i and float(vectors[i-1] @ vectors[i]) >= .72 and unit['end'] - grouped[-1]['start'] <= 3000:
                grouped[-1]['end'] = unit['end']
                grouped[-1]['text'] = text[grouped[-1]['start']:unit['end']]
            else:
                grouped.append({**unit, 'id':f'c{len(grouped)}'})
        return grouped
    boundaries = [0]
    if strategy != 'fixed':
        boundaries += [m.end() for m in re.finditer(r'\n\s*\n', text)]
    boundaries += [len(text)]
    result = []
    for begin, end in zip(boundaries, boundaries[1:]):
        while begin < end:
            stop = min(begin + 3000, end)
            if strategy == 'recursive' and stop < end:
                split = text.rfind('. ', begin + 1000, stop)
                if split > begin:
                    stop = split + 2
            if text[begin:stop].strip():
                result.append({'id': f'c{len(result)}', 'start': begin, 'end': stop, 'text': text[begin:stop]})
            begin = stop
    return result
