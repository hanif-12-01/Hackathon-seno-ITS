"""Read-only full outgoing Git blob/history scan; output only paths and categories.

Regex audit is not comprehensive secret certification. Does not fetch/push/rewrite.
"""
import json
import re
import subprocess

BASE = '0799a9375e9b1ced0b7a8745a9d19544cbef7194'


def git(*args, **kwargs):
    return subprocess.check_output(['git', '--no-pager', *args], **kwargs)


def audit():
    revision = BASE + '..HEAD'
    rows = git('rev-list', '--objects', revision).decode().splitlines()
    names = dict(row.split(' ', 1) for row in rows if ' ' in row)
    # rev-list lists one name per object; inspect every historical tree too so a
    # reused blob under a forbidden name cannot hide behind its benign alias.
    historical_paths = set()
    for commit in git('rev-list', revision).decode().splitlines():
        historical_paths.update(git('ls-tree', '-r', '--name-only', commit).decode().splitlines())
    # One batch process, inspect all objects including files deleted from later commits.
    stream = git('cat-file', '--batch', input=('\n'.join(row.split(' ', 1)[0]
                 for row in rows) + '\n').encode())
    patterns = {
        'private-key': rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        'provider-token': rb'(?:ghp_|github_pat_|AKIA|sk-proj-)[A-Za-z0-9_\-]{16,}',
        'database-url': rb'postgres(?:ql)?(?:\+psycopg)?://[^\s"\x27]+:[^\s"\x27]+@',
    }
    forbidden_pattern = r'(^|/)(private_runtime|artifacts|\.env(?:\.[^/]*)?|\.vercel|\.review|node_modules|\.venv|venv)(/|$)|\.(db|sqlite3|bak|dump|sql|log|tldr|tldraw)$'
    findings, count, cursor = [], 0, 0
    forbidden = [name for name in historical_paths if re.search(forbidden_pattern, name)]
    while cursor < len(stream):
        end = stream.index(b'\n', cursor)
        oid, kind, size = stream[cursor:end].decode().split()
        size = int(size)
        body = stream[end + 1:end + 1 + size]
        cursor = end + size + 2
        if kind != 'blob':
            continue
        count += 1
        name = names.get(oid, '<unnamed>')
        for label, pattern in patterns.items():
            if re.search(pattern, body):
                findings.append({'path': name, 'pattern': label, 'blob': oid[:12]})
    return {'base': BASE, 'outgoing_commits': int(git('rev-list', '--count', revision)),
            'outgoing_blobs': count, 'forbidden_paths': sorted(set(forbidden)),
            'findings': findings, 'scope': 'Every outgoing unique blob, including deleted historical files; regex only'}


if __name__ == '__main__':
    print(json.dumps(audit(), indent=2))
