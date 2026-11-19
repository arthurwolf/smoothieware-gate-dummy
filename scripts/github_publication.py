"""Derive bounded publication requests from already authenticated report data.

This module performs no network writes. Its caller owns API authentication,
complete bounded comment retrieval, freshness checks, serialization and journals.
"""
import json
import re

import dispatch_identity
from gate_result import validate_result
from mdkit import clip_markdown
from profiles import EXPECTED_STAGES, validate_manifest
import report

STAMP = re.compile(r'<!-- smoothie-gate-execution run=([1-9][0-9]*) attempt=([1-9][0-9]*) head=([0-9a-f]{40}|[0-9a-f]{64}) -->')


def _comment_target(comments, bot, workflow, head):
    # The transport requests one page of 100. A full page cannot prove that
    # later pages contain no matching/newer comment, so refuse it for now.
    if not isinstance(comments, list) or len(comments) >= 100:
        raise ValueError('comment inventory is incomplete or excessive')
    if (not isinstance(bot, dict) or not dispatch_identity._positive(bot.get('id'))
            or not isinstance(bot.get('login'), str) or not bot['login']):
        raise ValueError('expected publication bot identity is missing')
    matches = []
    for comment in comments:
        if not isinstance(comment, dict) or not isinstance(comment.get('user'), dict):
            raise ValueError('comment inventory contains malformed metadata')
        user = comment['user']
        if (type(user.get('id')) is not int or user['id'] != bot['id']
                or user.get('login') != bot['login'] or user.get('type') != 'Bot'):
            continue
        body = comment.get('body')
        if not isinstance(body, str):
            raise ValueError('bot comment has no text')
        if not body.startswith(report.MARKER + '\n'):
            continue
        if not dispatch_identity._positive(comment.get('id')) or len(body.encode('utf-8')) > 300000:
            raise ValueError('bot report metadata is invalid or excessive')
        lines = body.split('\n', 2)
        stamp = STAMP.fullmatch(lines[1]) if len(lines) > 1 else None
        if stamp is None:
            raise ValueError('existing bot report has no authenticated publication stamp')
        run, attempt = int(stamp[1]), int(stamp[2])
        if ((run, attempt) > (workflow['run_id'], workflow['attempt'])
                or (run == workflow['run_id'] and stamp[3] != head)):
            raise ValueError('existing bot report belongs to a newer or different execution')
        matches.append(comment['id'])
    if len(matches) > 1:
        raise ValueError('multiple bot reports require reconciliation')
    return matches[0] if matches else None


def plan(policy, binding, manifest, records, comments, bot):
    """Return data-only check/comment requests; never authorize their execution."""
    dispatch_identity.validate_binding_policy(binding, policy)
    workflow = binding['workflow']
    if (any(not dispatch_identity._positive(workflow.get(key)) for key in ('pr','run_id','attempt'))
            or any(not dispatch_identity._match(dispatch_identity.REVISION, binding.get(key))
                   for key in ('base','head'))):
        raise ValueError('authenticated publication identity is malformed')
    identity_problems = dispatch_identity.binding_problems(manifest, binding)
    if identity_problems:
        raise ValueError('; '.join(identity_problems))
    if not isinstance(records, list) or len(records) > 20:
        raise ValueError('report stage inventory is invalid or excessive')
    problems = validate_manifest(manifest, [r.get('stage') for r in records if isinstance(r, dict)])
    valid = []
    for record in records:
        issues = validate_result(record)
        try:
            json.dumps(record, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            issues.append('result is not finite JSON data')
        if issues:
            problems.extend(issues)
        else:
            valid.append(record)
    names = [record['stage'] for record in valid]
    if sorted(names) != sorted(EXPECTED_STAGES):
        problems.append('required stage set is missing, duplicated or unexpected')
    valid.sort(key=lambda record: (report.stage_rank(record['stage']), record['title']))
    status = report.overall_status(valid, problems)
    conclusion = 'success' if status in ('pass','warn') else 'failure'
    run_url = 'https://github.com/' + policy['repository'] + '/actions/runs/' + str(workflow['run_id'])
    rendered = report.render_comment(valid, problems, run_url, manifest=manifest)
    stamp = f"<!-- smoothie-gate-execution run={workflow['run_id']} attempt={workflow['attempt']} head={binding['head']} -->"
    # Bound the total comment as well as individual stage details. Preserve
    # the marker and provenance ahead of any truncated report content.
    content = rendered.removeprefix(report.MARKER + '\n').encode('utf-8')[:55000].decode('utf-8','ignore')
    body = report.MARKER + '\n' + stamp + '\n' + clip_markdown(content, 55000)
    selected = _comment_target(comments, bot, workflow, binding['head'])
    prefix = 'repos/' + policy['repository']
    return {'comment_method':'PATCH' if selected else 'POST',
            'comment_route':prefix + '/issues/comments/' + str(selected) if selected else
                            prefix + '/issues/' + str(workflow['pr']) + '/comments',
            'comment':{'body':body}, 'check_route':prefix + '/check-runs',
            'check':{'name':'smoothie-gate', 'head_sha':binding['head'], 'status':'completed',
                     'conclusion':conclusion, 'details_url':run_url,
                     'external_id':f"smoothie-gate:{workflow['pr']}:{workflow['run_id']}:{workflow['attempt']}",
                     'output':{'title':'Smoothie gate: ' + status,
                               'summary':'Complete report: ' + run_url}},
            'status':status}
