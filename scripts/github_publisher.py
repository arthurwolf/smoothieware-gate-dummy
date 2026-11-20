"""Authenticate, derive, journal and read back one trusted report publication.

The caller must serialize reporters for each PR. No contributor code or model
is executed here. Real publication requires external-action authorization.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ci_artifact
import dispatch_identity
from gate_result import make_result
import github_journal
import github_preflight
import github_publication
from profiles import EXPECTED_STAGES
from request_json import read_object
import report
import sandbox_process


def emit(operation, **fields):
    print(json.dumps({'type':'progress','operation':operation,**fields}),flush=True)


class ApiPublisher(github_preflight.ApiReader):
    """Bounded fixed-host metadata and one-shot JSON writes within one repo."""
    def __call__(self, route):
        prefix = re.escape('repos/' + self.repository)
        if re.fullmatch(prefix + r'/(?:check-runs/[1-9][0-9]*|commits/(?:[0-9a-f]{40}|[0-9a-f]{64})/check-runs\?per_page=100&page=1&filter=all|issues/(?:comments/[1-9][0-9]*|[1-9][0-9]*/comments\?per_page=100&page=1))', route):
            raw = self._request(route,2*1024**2,30)
            value = json.loads(raw.decode('utf-8'),object_pairs_hook=github_preflight._unique_object)
            expected = list if '/comments?per_page=' in route else dict
            if not isinstance(value,expected):raise ValueError('publication metadata has wrong shape')
            return value
        return super().__call__(route)

    def write(self, method, route, payload, journal):
        prefix = re.escape('repos/' + self.repository)
        pattern = (prefix + r'/(?:check-runs|issues/[1-9][0-9]*/comments)' if method=='POST' else
                   prefix + r'/issues/comments/[1-9][0-9]*' if method=='PATCH' else None)
        if pattern is None or not re.fullmatch(pattern,route) or not isinstance(payload,dict):
            raise ValueError('publication write is outside the configured repository')
        encoded = json.dumps(payload,allow_nan=False).encode('utf-8')
        if len(encoded)>256*1024:raise ValueError('publication request exceeds byte limit')
        source = Path(journal).with_name(Path(journal).name + '.request.json')
        with source.open('xb') as handle:
            handle.write(encoded);handle.flush();os.fsync(handle.fileno())
        captured = sandbox_process.run(['gh','api','--hostname','github.com','--method',method,
            '-H','Accept: application/vnd.github+json','-H','X-GitHub-Api-Version: 2026-03-10',
            route,'--input',str(source)],max_stdout_bytes=2*1024**2,max_stderr_bytes=8192,timeout_seconds=30)
        if captured.returncode or captured.problem:
            detail=captured.stderr.decode('utf-8','replace')[:300].replace('\n',' ')
            print(json.dumps({'type':'warning','operation':'publication write failed','method':method,'route':route,
                              'exit_code':captured.returncode,'problem':captured.problem,'stderr':detail}),flush=True)
            raise ValueError('publication API outcome is uncertain')
        response = json.loads(captured.stdout.decode('utf-8'),object_pairs_hook=github_preflight._unique_object)
        if not isinstance(response,dict):raise ValueError('publication response is not an object')
        return response


def _verified_write(api, method, route, payload, journal, kind, policy, binding, bot):
    prefix='repos/'+policy['repository']
    intent={'status':'outcome-uncertain','method':method,'route':route,'request':payload,
            'run_id':binding['workflow']['run_id'],'attempt':binding['workflow']['attempt'],
            'head':binding['head'],'message':'Reconcile the remote outcome before retrying.'}

    def operation():
        response=api.write(method,route,payload,journal)
        if not isinstance(response,dict) or not dispatch_identity._positive(response.get('id')):
            raise ValueError('publication response has no exact identifier')
        identifier=response['id']
        read_route=prefix+('/check-runs/' if kind=='check' else '/issues/comments/')+str(identifier)
        if method=='PATCH' and read_route!=route:raise ValueError('comment response changed identifier')
        for observed in (response,api(read_route)):
            if not isinstance(observed,dict) or type(observed.get('id')) is not int or observed['id']!=identifier:
                raise ValueError('publication readback has wrong identifier')
            if kind=='check':
                fields=('name','head_sha','status','conclusion','external_id')
                if any(observed.get(key)!=payload[key] for key in fields):
                    raise ValueError('check readback differs from authenticated verdict')
                # GitHub may replace the supplied link with the check's own page; accept only those two forms.
                if observed.get('details_url') not in (payload['details_url'],'https://github.com/'+policy['repository']+'/runs/'+str(identifier)):
                    raise ValueError('check readback differs from authenticated verdict')
                if observed.get('url')!='https://api.github.com/'+read_route:
                    raise ValueError('check readback has wrong repository')
            else:
                user=observed.get('user',{})
                if (not isinstance(user,dict) or type(user.get('id')) is not int or user['id']!=bot['id']
                        or user.get('login')!=bot['login'] or user.get('type')!='Bot'
                        or observed.get('body')!=payload['body']
                        or observed.get('issue_url')!='https://api.github.com/'+prefix+'/issues/'+str(binding['workflow']['pr'])):
                    raise ValueError('comment readback differs from owned PR report')
        return {'status':'published','receipt':{'id':identifier,'route':read_route,'head':binding['head']}}

    return github_journal.run_once(journal,intent,operation)


EXTERNAL_ID = re.compile(r'smoothie-gate:([1-9][0-9]*):([1-9][0-9]*):([1-9][0-9]*)')


def _refuse_newer_check(listing, binding, bot):
    """Refuse to publish over a newer publisher-owned check on the same head.

    A newer run may have published its check but lost its comment; the comment
    stamp alone cannot show that, so the check metadata is authoritative too.
    """
    if (not isinstance(listing,dict) or not isinstance(listing.get('check_runs'),list)
            or type(listing.get('total_count')) is not int or listing['total_count']!=len(listing['check_runs'])
            or len(listing['check_runs'])>=100):
        raise ValueError('check inventory is incomplete or excessive')
    if not isinstance(bot,dict):raise ValueError('expected publication bot identity is missing')
    workflow=binding['workflow']
    for check in listing['check_runs']:
        if not isinstance(check,dict) or check.get('name')!='smoothie-gate':continue
        app=check.get('app')
        if (not isinstance(app,dict) or type(bot.get('app_id')) is not int or app.get('id')!=bot['app_id']):
            continue
        found=EXTERNAL_ID.fullmatch(check.get('external_id') if isinstance(check.get('external_id'),str) else '')
        if found is None:raise ValueError('existing publisher check has no authenticated execution identity')
        if (int(found[2]),int(found[3]))>(workflow['run_id'],workflow['attempt']):
            raise ValueError('existing publisher check belongs to a newer execution')


def publish(policy, run_id, api, output, bot):
    root=Path(output).absolute()
    if (root.exists() or root.is_symlink() or not root.parent.is_dir() or root.parent.resolve()!=root.parent):
        raise ValueError('publication requires a fresh exclusively owned directory')
    identity,pr,binding=github_preflight.authenticate_inputs(policy,run_id,api)
    root.mkdir()
    emit('authenticate completed gate report',repository=policy['repository'],pr=identity.pr,run_id=run_id)
    selected=None
    try:
        selected=github_preflight.download_results(policy,run_id,api,root/'downloaded')
        if selected['manifest_binding']!=binding:raise ValueError('artifact binding changed')
        ci_artifact.assemble(root/'downloaded',binding,pr,root/'validated')
        manifest=read_object(root/'validated/manifest.json',5*1024**2)
        records,problems=report.load_results(root/'validated')
        if problems:raise ValueError('validated artifact has incomplete stages')
    except (OSError,ValueError,RecursionError):
        # Missing/rejected data gets an explicit failure on the authenticated
        # firmware head, never a green check or artifact-derived route.
        manifest=dict(binding)
        records=[make_result(stage,'error',stage,'Report artifact unavailable or rejected') for stage in EXPECTED_STAGES]
        emit('report incomplete artifact',repository=policy['repository'],pr=identity.pr)

    def fresh():
        current,_,observed=github_preflight.authenticate_inputs(policy,run_id,api)
        if current!=identity or observed!=binding:raise ValueError('publication execution is stale')
        if selected is not None and github_preflight.prepare(policy,run_id,api)!=selected:
            raise ValueError('publication artifact changed')

    comment_route='repos/'+policy['repository']+'/issues/'+str(identity.pr)+'/comments?per_page=100&page=1'
    checks_route='repos/'+policy['repository']+'/commits/'+binding['head']+'/check-runs?per_page=100&page=1&filter=all'
    def current_plan():
        _refuse_newer_check(api(checks_route),binding,bot)
        comments=api(comment_route)
        prepared=github_publication.plan(policy,binding,manifest,records,comments,bot)
        fresh()
        return prepared

    prepared=current_plan()
    (root/'report').mkdir()
    (root/'report/report.html').write_text(report.render_html(prepared['comment']['body']),encoding='utf-8')
    emit('publish firmware-head check',pr=identity.pr,head=binding['head'],status=prepared['status'])
    check=_verified_write(api,'POST',prepared['check_route'],prepared['check'],root/'check.json','check',policy,binding,bot)
    if check['status']!='published':return check
    prepared=current_plan()
    emit('publish owned pull-request report',pr=identity.pr,method=prepared['comment_method'])
    comment=_verified_write(api,prepared['comment_method'],prepared['comment_route'],prepared['comment'],
                            root/'comment.json','comment',policy,binding,bot)
    if comment['status']!='published':return comment
    fresh()
    return {'status':'published','verdict':prepared['status'],'check':check['receipt'],'comment':comment['receipt']}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy',required=True)
    parser.add_argument('--run-id',required=True,type=int)
    parser.add_argument('--out',required=True)
    args=parser.parse_args(argv)
    try:
        policy=read_object(args.policy,65536)
        result=publish(policy,args.run_id,ApiPublisher(policy['repository']),args.out,policy.get('publisher_bot'))
        print(json.dumps({'type':'result',**result,'output':args.out}))
        if result.get('status')!='published':return 2
        return {'pass':0,'warn':0,'fail':1}.get(result.get('verdict'),2)
    except (OSError,ValueError,KeyError,RecursionError) as error:
        print(json.dumps({'type':'error','operation':'publish trusted gate report','message':str(error),'output':args.out}))
        return 2


if __name__=='__main__':
    sys.exit(main())
