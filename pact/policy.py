"""Narrow policy checks produce review work, never a legal enforceability opinion."""
import re


def assess(findings, playbook):
    results = []
    for topic in ['liability', 'assignment', 'confidentiality']:
        candidates = [f for f in findings if f['kind'] == topic and f['executed']]
        if not candidates:
            results.append({'topic':topic,'status':'not_found','reason':'No verified evidence; manual review required','finding_ids':[]})
            continue
        latest = max(f['ordinal'] for f in candidates)
        selected = [f for f in candidates if f['ordinal']==latest]
        text = ' '.join(f.get('context',f['quote']) for f in selected)
        status, reason = 'needs_review', 'Verify the complete clause and exceptions against the playbook'
        if topic == 'liability':
            months = set(int(x) for x in re.findall(r'\b(\d{1,3})\s+months?\b',text,re.I))
            if re.search(r'\b(?:unlimited|uncapped)\s+liability\b',text,re.I):
                status, reason = 'deviation', 'Source explicitly describes uncapped or unlimited liability'
            elif len(months)==1:
                value = next(iter(months))
                status = 'deviation' if value > playbook['max_liability_months'] else 'within_numeric_threshold'
                reason = f"Source mentions a {value}-month fee period; playbook threshold is {playbook['max_liability_months']}. Exceptions still require review."
        elif topic == 'assignment' and playbook['require_assignment_consent']:
            if re.search(r'\b(?:may|can)\s+assign\b.{0,100}\bwithout\b.{0,50}\bconsent\b',text,re.I):
                status, reason = 'deviation', 'Source permits assignment without consent; inspect any exceptions'
            elif re.search(r'\b(?:requires?|prior written)\s+consent\b',text,re.I):
                status, reason = 'consent_language_found', 'Consent language is present; reviewer must verify its scope'
        results.append({'topic':topic,'status':status,'reason':reason,'finding_ids':[f['id'] for f in selected]})
    return results
