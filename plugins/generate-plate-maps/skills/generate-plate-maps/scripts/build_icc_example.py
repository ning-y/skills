#!/usr/bin/env python3
"""Build the explicit ICC specification; experiment logic lives here, not in renderer."""
import argparse
import json
from pathlib import Path
from allocation import allocate_grouped_positions

DONORS=['ES345M','ES296F','ES363M','EsDermFib']
STAINS=['p16','p21','gH2AX','2° only anti-mouse','2° only anti-rabbit']
PROTOCOLS={
    'DOXO':dict(dose='500 nM',exposure='24 h pulse on D0',fixation='D10–12',reference='[1]'),
    'CIS':dict(dose='1 µg/mL',exposure='5 days',fixation='After 5 days of treatment',reference='[2]')}
PROTOCOL_NOTES=('No technical replicates.\n'
    '[1] Doxorubicin: 500 nM, 24 h pulse on D0; fix on D10–12.\n'
    '[2] Cisplatin: 1 µg/mL for 5 days; then fix.')


def well_annotations(donor,treatment,stain):
    attributes=dict(donor=donor,treatment=treatment,stain=stain)
    lines=[treatment]
    if treatment in PROTOCOLS:
        attributes.update(PROTOCOLS[treatment])
        lines.append(PROTOCOLS[treatment]['dose'])
    stain_label=stain.replace('2° only ','2° only\n').replace('Sudan black','Sudan\nblack')
    lines.append(stain_label)
    return attributes,'\n'.join(lines)



def categories(values, colors):
    return [dict(value=v,color=c) for v,c in zip(values,colors)]


def build_example():
    views=[
        dict(id='donor',name='donor',field='donor',
             categories=categories(DONORS,['#91BCE0','#E6AD91','#ACD0B5','#C2B1DC']),
             legend=dict(title='Donor')),
        dict(id='treatment',name='treatment',field='treatment',
             categories=categories(['UT','DOXO','CIS'],['#C9D1DA','#EBA394','#9BBBE0']),
             legend=dict(title='Treatment / category')),
        dict(id='stain',name='stain',field='stain',
             categories=categories(STAINS,['#9DC6E5','#E9C978','#AFCFB8','#C6B2DC','#E5B3C9']),
             legend=dict(title='Stain / assay target'))]
    plates=[]
    for number,drug,name,secondary_treatment in [
        (1,'DOXO','doxorubicin','DOXO'),(2,'CIS','cisplatin','CIS')]:
        # Ordered assay assignments; secondary-only controls receive the plate drug.
        assignments=[('UT','p16'),('UT','p21'),('UT','gH2AX'),
                     (drug,'p16'),(drug,'p21'),(drug,'gH2AX'),
                     (secondary_treatment,'2° only anti-mouse'),
                     (secondary_treatment,'2° only anti-rabbit')]
        rows=list('ABCDEFGH')
        grouped=allocate_grouped_positions(rows,12,len(DONORS),len(assignments))
        allocated={position:(donor,assignment) for donor,positions in zip(DONORS,grouped)
                   for position,assignment in zip(positions,assignments)}
        wells=[]
        for row in rows:
            for col in range(1,13):
                well=dict(row=row,column=col,status='empty',attributes={},label='empty')
                if (row,col) in allocated:
                    donor,(treatment,stain)=allocated[(row,col)]
                    attributes,label=well_annotations(donor,treatment,stain)
                    well.update(status='occupied',attributes=attributes,label=label)
                wells.append(well)
        plates.append(dict(id=f'plate-{number}',title=f'Plate {number} – {name} plate',
                           rows=list('ABCDEFGH'),columns=12,wells=wells))
    return dict(schema_version=1,experiment=dict(title='Bat macrophage senescence marker optimization',
        subtitle='Facet columns = plate; facet rows = view',footer=PROTOCOL_NOTES,
        metadata=dict(technical_replicates=False)),plates=plates,views=views,
        style=dict(cell_inches=0.75,font_size=8.7,min_font_size=7.0,dpi=300))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(build_example(),ensure_ascii=False,indent=2)+'\n')
