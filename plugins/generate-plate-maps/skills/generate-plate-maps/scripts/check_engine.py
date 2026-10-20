#!/usr/bin/env python3
"""Regression checks for biological assignments, rejected inputs and determinism."""
import copy
import hashlib
import json
import tempfile
from pathlib import Path
import matplotlib.pyplot as plt
from build_icc_example import build_example
from plate_maps import validate_spec, render, export, row_name, plate_geometry, resolve_spec
from build_mixed_example import build_mixed_example
from allocation import allocate_grouped_positions


def rejects(spec):
    try:
        validate_spec(spec)
    except ValueError:
        return
    raise AssertionError('Invalid specification was accepted')


def run():
    s=build_example()
    validate_spec(s)
    p1,p2=s['plates']
    assert sum(w['status']=='occupied' for w in p1['wells'])==32
    assert sum(w['status']=='empty' for w in p2['wells'])==64
    for p,expected_drug,secondary in [(p1,'DOXO','DOXO'),(p2,'CIS','CIS')]:
        for w in p['wells']:
            if w['row'] in 'CDEF' and 3<=w['column']<=10:
                expected='UT' if w['column']<=5 else expected_drug if w['column']<=8 else secondary
                assert w['attributes']['treatment']==expected
                assert w['attributes']['donor']==['ES345M','ES296F','ES363M','EsDermFib']['CDEF'.index(w['row'])]
            else:
                assert w['status']=='empty'
    for mutation in (
        lambda x:x['plates'][0]['wells'].pop(),
        lambda x:x['plates'][0]['wells'].append(copy.deepcopy(x['plates'][0]['wells'][0])),
        lambda x:x['plates'][0]['wells'][0].update(column=13),
        lambda x:x['plates'][0]['wells'][26]['attributes'].update(treatment='UNKNOWN'),
        lambda x:x['plates'][0]['wells'][8]['attributes'].update(donor='ES345M'),
        lambda x:x['style'].update(dpi=True),
        lambda x:x.update(typo=True),
    ):
        bad=copy.deepcopy(s)
        mutation(bad)
        rejects(bad)
    # A compact generic plate exercises the renderer without ICC-specific logic.
    tiny=dict(schema_version=1,experiment=dict(title='Test'),plates=[dict(id='p',title='P',
        rows=['A','B'],columns=3,wells=[dict(row=r,column=c,status='occupied' if c==1 else 'empty',
        attributes={'sample':'S'} if c==1 else {},label='S' if c==1 else '') for r in 'AB' for c in range(1,4)])],
        views=[dict(id='s',name='s',field='sample',categories=[dict(value='S',color='#89BADD')],
        legend=dict(title='Sample'))],style=dict(panel_title_size=8,title_size=12,legend_columns=2,dpi=80))
    assert row_name(25)=='Z' and row_name(26)=='AA'
    for geometry in [(2,3),(3,4),(4,6),(6,8),(8,12),(16,24)]:
        trial=copy.deepcopy(tiny)
        p=trial['plates'][0]
        p['rows']=[row_name(i) for i in range(geometry[0])]
        p['columns']=geometry[1]
        p['wells']=[dict(row=r,column=c,status='occupied' if c==1 else 'empty',
            attributes={'sample':'S'} if c==1 else {},label='S' if c==1 else '')
            for r in p['rows'] for c in range(1,p['columns']+1)]
        fig=render(trial)
        plt.close(fig)
    for count,geometry in [(6,(2,3)),(12,(3,4)),(24,(4,6)),(48,(6,8)),(96,(8,12)),(384,(16,24))]:
        rows,cols=plate_geometry(count)
        assert (len(rows),cols)==geometry
        trial=copy.deepcopy(tiny)
        p=trial['plates'][0]
        p.pop('rows')
        p.pop('columns')
        p['format']=count
        p['wells']=[dict(row=r,column=c,status='occupied' if c==1 else 'empty',
            attributes={'sample':'S'} if c==1 else {},label='S' if c==1 else '')
            for r in rows for c in range(1,cols+1)]
        before=copy.deepcopy(trial)
        validate_spec(trial)
        normalized=resolve_spec(trial)
        assert trial==before and len(normalized['plates'][0]['rows'])*normalized['plates'][0]['columns']==count
        bad=copy.deepcopy(trial)
        bad['plates'][0]['columns']=999
        rejects(bad)
    bad=copy.deepcopy(tiny)
    bad['plates'][0]['format']=7
    rejects(bad)
    mixed=build_mixed_example()
    validate_spec(mixed)
    assert mixed['plates'][:2]==[dict(p,format=96) for p in s['plates']]
    for p,drug in zip(mixed['plates'][2:],['DOXO','CIS']):
        assert len(p['wells'])==24
        assert sum(w['status']=='occupied' for w in p['wells'])==8
        expected={}
        for column,donor in enumerate(['ES345M','ES296F','ES363M','EsDermFib'],start=2):
            expected[('B',column)]=(donor,'UT')
            expected[('C',column)]=(donor,drug)
        for w in p['wells']:
            pos=(w['row'],w['column'])
            if pos in expected:
                donor,treatment=expected[pos]
                assert all(w['attributes'][k]==v for k,v in dict(donor=donor,treatment=treatment,stain='Sudan black').items())
            else:
                assert w['status']=='empty'
    assert mixed['experiment']['title']=='Bat macrophage senescence marker optimization'
    for p in mixed['plates']:
        for w in p['wells']:
            treatment=w['attributes'].get('treatment')
            if treatment in ('DOXO','CIS'):
                dose,exposure,fixation,reference=(
                    ('500 nM','24 h pulse on D0','D10–12','[1]') if treatment=='DOXO' else
                    ('1 µg/mL','5 days','After 5 days of treatment','[2]'))
                assert all(w['attributes'][k]==v for k,v in
                    dict(dose=dose,exposure=exposure,fixation=fixation,reference=reference).items())
                assert dose in w['label']
            else:
                assert 'dose' not in w['attributes']
    assert '[1] Doxorubicin: 500 nM, 24 h pulse on D0; fix on D10–12.' in mixed['experiment']['footer']
    assert '[2] Cisplatin: 1 µg/mL for 5 days; then fix.' in mixed['experiment']['footer']

    # Capacity and uniqueness across every feasible group count on common formats.
    for count in (6,12,24,48,96,384):
        rows,cols=plate_geometry(count)
        for group_size in (1,2,3,8):
            for ngroups in range(1,count//group_size+1):
                groups=allocate_grouped_positions(rows,cols,ngroups,group_size)
                positions=[pos for group in groups for pos in group]
                assert len(positions)==ngroups*group_size==len(set(positions))
                assert all(r in rows and 1<=c<=cols for r,c in positions)
                interior_capacity=max(0,len(rows)-2)*max(0,cols-2)
                interior_used=sum(r not in (rows[0],rows[-1]) and c not in (1,cols) for r,c in positions)
                assert interior_used==min(len(positions),interior_capacity)
    assert allocate_grouped_positions(list('ABCDEFGH'),12,4,8)[0]==[('C',c) for c in range(3,11)]
    assert allocate_grouped_positions(list('ABCD'),6,4,2)==[
        [('B',2),('B',3)],[('B',4),('B',5)],[('C',2),('C',3)],[('C',4),('C',5)]]
    pairs=allocate_grouped_positions(list('ABC'),4,4,2)
    for (r1,c1),(r2,c2) in pairs:
        assert abs('ABC'.index(r1)-'ABC'.index(r2))+abs(c1-c2)==1
    try:
        allocate_grouped_positions(['A','B'],3,4,2)
    except ValueError:
        pass
    else:
        raise AssertionError('Allocation exceeded capacity')
    mixed['style']['dpi']=45
    for mode in ['fit','shared_pitch']:
        mixed['style']['plate_scale']=mode
        fig=render(mixed)
        assert len(fig.axes)==15
        assert len(fig.axes[2].patches)==25  # 24 wells + panel, not a padded 96-well grid
        plt.close(fig)
    multiline=copy.deepcopy(tiny)
    multiline['experiment']['footer']='Protocol 1\nProtocol 2\nProtocol 3'
    base=render(tiny)
    with_notes=render(multiline)
    assert with_notes.get_figheight()>base.get_figheight()
    plt.close(base)
    plt.close(with_notes)
    circles=copy.deepcopy(tiny)
    circles['style'].update(well_shape='circle',show_empty=False)
    fig=render(circles)
    plt.close(fig)
    overflow=copy.deepcopy(tiny)
    overflow['plates'][0]['wells'][0]['label']='UnbreakableScientificLabel'*30
    try:
        render(overflow)
    except ValueError as e:
        assert 'does not fit' in str(e)
    else:
        raise AssertionError('Overflow was silently accepted')
    with tempfile.TemporaryDirectory() as td:
        for name in ['a','b']:
            export(tiny,Path(td)/name)
        for suffix in ['.png','.svg','.pdf','.json','.csv','.manifest.json']:
            a,b=[(Path(td)/(n+suffix)).read_bytes() for n in ['a','b']]
            assert a==b,f'Non-deterministic {suffix}'
    print('PASS: corrected CIS controls, interior-first allocation/capacity, ICC assignments, seven invalid input cases, 6/12/24/48/96/384-well presets/rendering, format conflict rejection, mixed 96-/24-well assignments/scaling, circles/hidden empties, overflow rejection, repeat-export byte equality.')

if __name__=='__main__':
    run()
