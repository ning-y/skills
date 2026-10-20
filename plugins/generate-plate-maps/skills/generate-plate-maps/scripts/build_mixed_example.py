#!/usr/bin/env python3
"""Extend the ICC experiment with two 24-well Sudan black plates."""
import argparse
import json
from pathlib import Path
from build_icc_example import build_example, DONORS, well_annotations
from plate_maps import plate_geometry
from allocation import allocate_grouped_positions


def build_mixed_example():
    spec=build_example()
    spec['experiment'].update(title='Bat macrophage senescence marker optimization')
    spec['experiment']['metadata']['sudan_black_layout_note']=(
        'One well per donor-treatment combination; UT at B2-B5, drug at C2-C5; '
        'donors aligned in columns 2-5 in the specified order.')
    spec['style'].update(show_empty=True,plate_scale='fit')
    for p in spec['plates']:
        p['format']=96
    for number,drug,name in [(3,'DOXO','doxorubicin'),(4,'CIS','cisplatin')]:
        rows,columns=plate_geometry(24)
        grouped=allocate_grouped_positions(rows,columns,2,len(DONORS))
        allocated={position:(donor,treatment) for treatment,positions in zip(['UT',drug],grouped)
                   for donor,position in zip(DONORS,positions)}
        wells=[]
        for row in rows:
            for col in range(1,columns+1):
                w=dict(row=row,column=col,status='empty',attributes={},label='empty')
                if (row,col) in allocated:
                    donor,treatment=allocated[(row,col)]
                    attributes,label=well_annotations(donor,treatment,'Sudan black')
                    w.update(status='occupied',attributes=attributes,label=label)
                wells.append(w)
        spec['plates'].append(dict(id=f'plate-{number}',title=f'Plate {number} – {name} plate',
            format=24,wells=wells,metadata=dict(assay='Sudan black',
            technical_replicates=False,layout_basis='One well per donor-treatment combination')))
    spec['views'][2]['categories'].append(dict(value='Sudan black',color='#9BA7AB'))
    return spec


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(build_mixed_example(),ensure_ascii=False,indent=2)+'\n')
