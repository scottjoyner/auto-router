import json,re

def unique_object(pairs):
    obj={}
    for k,v in pairs:
        if k in obj: raise ValueError('duplicate key')
        obj[k]=v
    return obj

def expected_for(kind,items,resource_hold=True):
    if kind not in ('damage','pending') or type(items) is not list or not items or len(items)>256: raise ValueError('invalid task')
    result={}
    for row in items:
        if type(row) is not dict or type(row.get('id')) is not str or not re.fullmatch(r'[DS][1-9][0-9]*',row['id']) or row['id'] in result: raise ValueError('invalid identity')
        k=row['id']
        if kind=='damage':
            a,b=row.get('decoded'),row.get('expected')
            if type(a) is not int or type(b) is not int or a<0 or b<=0 or a>b: raise ValueError('invalid frame counts')
            result[k]='PRESERVE' if a<b else 'REVIEW'
        else:
            if resource_hold is not True: raise ValueError('unknown gate; no promotion')
            if type(row.get('bytes')) is not int or row['bytes']<=0 or type(row.get('boxes')) is not list: raise ValueError('invalid extent')
            result[k]='QUEUED'
    return result

def validate(kind,items,response,resource_hold=True):
    try: expected=expected_for(kind,items,resource_hold)
    except (ValueError,TypeError): return {'accepted':False,'reason':'INVALID_TASK','authoritative':None,'release_allowed':False}
    result={'accepted':False,'authoritative':expected,'release_allowed':False}
    if type(response) is not str or len(response)>16384: return dict(result,reason='INVALID_RESPONSE')
    try: actual=json.loads(response,object_pairs_hook=unique_object,parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
    except (ValueError,TypeError,RecursionError): return dict(result,reason='INVALID_JSON')
    if type(actual) is not dict or set(actual)!=set(expected) or any(type(v) is not str for v in actual.values()): return dict(result,reason='SCHEMA_MISMATCH')
    if actual!=expected: return dict(result,reason='SEMANTIC_MISMATCH',mismatched=sorted(k for k in expected if actual[k]!=expected[k]))
    return dict(result,accepted=True,reason='PASS')


def evaluate_recovery_response(kind,items,payload,resource_hold=True):
    """Opt-in trusted caller contract; never promotes custody or admits a provider."""
    denied={'accepted':False,'release_allowed':False,'reason':'INVALID_PROVIDER_ENVELOPE'}
    if type(payload) is not dict: return denied
    choices=payload.get('choices')
    if type(choices) is not list or len(choices)!=1 or type(choices[0]) is not dict: return denied
    choice=choices[0]; message=choice.get('message')
    if choice.get('finish_reason')!='stop' or type(message) is not dict or message.get('tool_calls') or message.get('function_call'): return denied
    if message.get('role')!='assistant' or type(message.get('content')) is not str: return denied
    return validate(kind,items,message['content'],resource_hold)
