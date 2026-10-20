#!/usr/bin/env python3
"""Validated, deterministic categorical plate maps: stdlib + Matplotlib only."""
from __future__ import annotations
import argparse
import csv
import copy
import hashlib
import io
import json
import math
import platform
from collections import Counter
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import is_color_like, to_rgb
from matplotlib.patches import Circle, FancyBboxPatch, Patch

VERSION = '1.3.0'
PLATE_FORMATS = {6: (2, 3), 12: (3, 4), 24: (4, 6),
                 48: (6, 8), 96: (8, 12), 384: (16, 24)}
DEFAULT_STYLE = dict(cell_inches=0.65, font_size=8.2, min_font_size=6.0,
    title_size=20, panel_title_size=11, legend_font_size=10, dpi=300,
    font_family='DejaVu Sans', empty_color='#E9EDF1', empty_text_color='#8B929B',
    border_color='#B1BBC6', show_empty=True, empty_label='empty',
    well_shape='rounded', legend_columns=6, plate_scale='fit')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def keys(obj, required, optional, path):
    require(isinstance(obj, dict), f'{path}: expected object')
    require(not (set(required)-obj.keys()), f'{path}: missing keys {set(required)-obj.keys()}')
    require(not (obj.keys()-set(required)-set(optional)),
            f'{path}: unknown keys {obj.keys()-set(required)-set(optional)}')


def nonempty(value, path):
    require(isinstance(value, str) and bool(value.strip()), f'{path}: expected nonempty string')


def positive_integer(value):
    return type(value) is int and value > 0


def row_name(index):
    """Zero-based index to A..Z, AA..AZ, etc."""
    result = ''
    index += 1
    while index:
        index, digit = divmod(index-1, 26)
        result = chr(65+digit)+result
    return result


def plate_geometry(well_count):
    """Resolve a named common format to explicit rows and columns."""
    require(type(well_count) is int and well_count in PLATE_FORMATS,
            f'Unsupported format {well_count!r}; choose {list(PLATE_FORMATS)} or explicit rows/columns')
    nrows, ncols = PLATE_FORMATS[well_count]
    return [row_name(i) for i in range(nrows)], ncols


def resolve_spec(spec):
    """Expand format shorthand without inventing well assignments or mutating input."""
    resolved = copy.deepcopy(spec)
    if isinstance(resolved, dict) and isinstance(resolved.get('plates'), list):
        for p in resolved['plates']:
            if isinstance(p, dict) and 'format' in p:
                rows, columns = plate_geometry(p['format'])
                require('rows' not in p or p['rows'] == rows, 'format conflicts with explicit rows')
                require('columns' not in p or (type(p['columns']) is int and p['columns'] == columns),
                        'format conflicts with explicit columns')
                p.update(rows=rows, columns=columns)
    return resolved


def validate_spec(spec):
    """Fail on unsupported configuration, omitted wells or ambiguous assignments."""
    spec = resolve_spec(spec)
    keys(spec, ['schema_version','experiment','plates','views'], ['style'], 'spec')
    require(type(spec['schema_version']) is int and spec['schema_version']==1, 'Unsupported schema_version')
    exp = spec['experiment']
    keys(exp, ['title'], ['subtitle','footer','metadata'], 'experiment')
    nonempty(exp['title'], 'experiment.title')
    for f in ('subtitle','footer'):
        require(isinstance(exp.get(f,''), str), f'experiment.{f}: expected string')
    require(isinstance(exp.get('metadata',{}),dict), 'experiment.metadata must be object')
    for f in ('plates','views'):
        require(isinstance(spec[f],list) and bool(spec[f]), f'{f}: expected nonempty list')
    keys(spec.get('style',{}), [], DEFAULT_STYLE, 'style')
    style = {**DEFAULT_STYLE, **spec.get('style',{})}
    for f in ('cell_inches','font_size','min_font_size','title_size','panel_title_size','legend_font_size'):
        v = style[f]
        require(type(v) in (int,float) and math.isfinite(v) and v>0, f'style.{f}: positive finite number required')
    require(style['min_font_size']<=style['font_size'], 'min_font_size exceeds font_size')
    for f in ('dpi','legend_columns'):
        require(positive_integer(style[f]), f'style.{f}: positive integer required')
    for f in ('empty_color','empty_text_color','border_color'):
        require(is_color_like(style[f]), f'style.{f}: invalid color')
    require(type(style['show_empty']) is bool, 'show_empty must be boolean')
    require(isinstance(style['empty_label'],str), 'empty_label must be string')
    nonempty(style['font_family'], 'font_family')
    font_manager.findfont(style['font_family'],fallback_to_default=False)
    require(style['well_shape'] in ('rounded','circle'), 'well_shape must be rounded or circle')
    require(style['plate_scale'] in ('fit', 'shared_pitch'), 'plate_scale must be fit or shared_pitch')
    view_ids = []
    for v in spec['views']:
        keys(v, ['id','name','field','categories'], ['legend','label_fields'], 'view')
        for f in ('id','name','field'):
            nonempty(v[f],f'view.{f}')
        view_ids.append(v['id'])
        require(isinstance(v['categories'],list) and bool(v['categories']), 'categories must be nonempty list')
        values=[]
        for c in v['categories']:
            keys(c,['value','color'],['label'],'category')
            nonempty(c['value'],'category.value')
            require(c['value']!='empty', 'empty is reserved for unoccupied wells')
            require(is_color_like(c['color']), 'Invalid category color')
            require(isinstance(c.get('label',c['value']),str), 'category.label must be string')
            values.append(c['value'])
        require(len(values)==len(set(values)), f'Duplicate categories in {v["id"]}')
        lg=v.get('legend',{})
        keys(lg,[],['title','include_empty','include_unused'],'legend')
        require(isinstance(lg.get('title',v['name']),str),'legend.title must be string')
        for f in ('include_empty','include_unused'):
            require(type(lg.get(f,True)) is bool, f'legend.{f} must be boolean')
        if 'label_fields' in v:
            require(isinstance(v['label_fields'],list) and bool(v['label_fields']), 'label_fields must be nonempty list')
            for f in v['label_fields']:
                nonempty(f,'label_fields entry')
    require(len(view_ids)==len(set(view_ids)), 'Duplicate view IDs')
    plate_ids=[]
    for p in spec['plates']:
        keys(p,['id','title','rows','columns','wells'],['metadata','format'],'plate')
        for f in ('id','title'):
            nonempty(p[f],f'plate.{f}')
        plate_ids.append(p['id'])
        require(isinstance(p.get('metadata',{}),dict),'plate.metadata must be object')
        rows=p['rows']
        require(isinstance(rows,list) and bool(rows),'rows must be nonempty list')
        for r in rows:
            nonempty(r,'row label')
        require(len(rows)==len(set(rows)),'Duplicate row labels')
        require(positive_integer(p['columns']),'columns must be positive integer')
        require(isinstance(p['wells'],list),'wells must be list')
        positions=set()
        for w in p['wells']:
            keys(w,['row','column','status','attributes'],['label','labels_by_view'],'well')
            require(isinstance(w['row'],str) and w['row'] in rows,'Invalid row')
            require(positive_integer(w['column']) and w['column']<=p['columns'],'Invalid column')
            pos=(w['row'],w['column'])
            require(pos not in positions,f'Duplicate well {p["id"]}:{pos}')
            positions.add(pos)
            require(w['status'] in ('occupied','empty'),'status must be occupied or empty')
            attrs=w['attributes']
            require(isinstance(attrs,dict),'attributes must be object')
            require(all(isinstance(k,str) and isinstance(x,str) and x.strip() for k,x in attrs.items()),
                    'attributes must contain nonempty string values')
            require(isinstance(w.get('label',''),str),'well.label must be string')
            labels=w.get('labels_by_view',{})
            require(isinstance(labels,dict) and set(labels)<=set(view_ids),'Invalid labels_by_view keys')
            require(all(isinstance(x,str) for x in labels.values()),'labels_by_view values must be strings')
            if w['status']=='empty':
                require(not attrs,'Empty wells must have empty attributes')
                continue
            for v in spec['views']:
                allowed={c['value'] for c in v['categories']}
                require(attrs.get(v['field']) in allowed,
                        f'{p["id"]} {pos}: missing/unknown {v["field"]} category {attrs.get(v["field"])!r}')
                if 'label_fields' in v:
                    require(set(v['label_fields'])<=attrs.keys(),f'{pos}: missing label field')
            nonempty(w.get('label',''),f'{p["id"]} {pos}: label')
        require(len(positions)==len(rows)*p['columns'],f'{p["id"]}: every well must be explicit, including empties')
    require(len(plate_ids)==len(set(plate_ids)),'Duplicate plate IDs')
    return style


def label_for(w,view,style):
    if view['id'] in w.get('labels_by_view',{}):
        return w['labels_by_view'][view['id']]
    if w['status']=='empty':
        return w.get('label',style['empty_label'])
    if 'label_fields' in view:
        return '\n'.join(w['attributes'][f] for f in view['label_fields'])
    return w['label']


def contrast_color(color):
    rgb=to_rgb(color)
    linear=[c/12.92 if c<=0.04045 else ((c+0.055)/1.055)**2.4 for c in rgb]
    lum=sum(c*w for c,w in zip(linear,(0.2126,0.7152,0.0722)))
    return '#15202B' if (lum+0.05)/0.05 >= 1.05/(lum+0.05) else '#FFFFFF'


def render_panel(ax,plate,view,style,max_rows,max_cols):
    """Render one view from the unchanged physical well records."""
    ax.set_xlim(-0.95,max_cols+0.25)
    ax.set_ylim(max_rows+0.22,-1.32)
    ax.set_aspect('equal')
    ax.axis('off')
    ax.add_patch(FancyBboxPatch((-0.9,-1.27),max_cols+1.08,max_rows+1.43,
        boxstyle='round,pad=0.015,rounding_size=0.15',facecolor='white',
        edgecolor='#CED6DF',linewidth=0.9,zorder=0))
    title=ax.text(-0.57,-0.91,f'{plate["title"]} | View: {view["name"]}',
        fontsize=style['panel_title_size'],fontweight='bold',color='#1F3043',va='center')
    constraints=[(title,(-0.57,-1.15,max_cols-0.08,-0.67),False)]
    for col in range(1,plate['columns']+1):
        ax.text(col-0.5,-0.24,str(col),ha='center',va='center',fontsize=10,color='#526170')
    for row,name in enumerate(plate['rows']):
        ax.text(-0.34,row+0.5,name,ha='center',va='center',fontsize=10,fontweight='bold',color='#526170')
    palette={c['value']:c['color'] for c in view['categories']}
    row_index={r:i for i,r in enumerate(plate['rows'])}
    for w in sorted(plate['wells'],key=lambda w:(row_index[w['row']],w['column'])):
        empty=w['status']=='empty'
        if empty and not style['show_empty']:
            continue
        x,y=w['column']-0.5,row_index[w['row']]+0.5
        color=style['empty_color'] if empty else palette[w['attributes'][view['field']]]
        options=dict(facecolor=color,edgecolor=style['border_color'],linewidth=0.65)
        if style['well_shape']=='circle':
            ax.add_patch(Circle((x,y),0.44,**options))
            halfw,halfh=0.295,0.295
        else:
            ax.add_patch(FancyBboxPatch((x-0.44,y-0.43),0.88,0.86,
                boxstyle='round,pad=0,rounding_size=0.10',**options))
            halfw,halfh=0.39,0.37
        text=ax.text(x,y,label_for(w,view,style),ha='center',va='center',
            fontsize=style['font_size'],linespacing=1.15,
            color=style['empty_text_color'] if empty else contrast_color(color))
        constraints.append((text,(x-halfw,y-halfh,x+halfw,y+halfh),True))
    return constraints


def legend_entries(spec,view,style):
    used={w['attributes'][view['field']] for p in spec['plates'] for w in p['wells'] if w['status']=='occupied'}
    settings=view.get('legend',{})
    entries=[(c.get('label',c['value']),c['color']) for c in view['categories']
             if settings.get('include_unused',True) or c['value'] in used]
    if settings.get('include_empty',True):
        entries.append(('empty',style['empty_color']))
    return entries


def render(spec):
    spec=resolve_spec(spec)
    style=validate_spec(spec)
    mr=max(len(p['rows']) for p in spec['plates'])
    mc=max(p['columns'] for p in spec['plates'])
    np,nv=len(spec['plates']),len(spec['views'])
    cell=style['cell_inches']
    pw,ph=(mc+1.2)*cell,(mr+1.54)*cell
    gap,margin,header=0.35,0.4,1.0
    footer_lines=len(spec['experiment'].get('footer','').splitlines())
    footer=max(0.4,0.17+footer_lines*12/72+0.12)
    legend_heights=[0.35+0.30*math.ceil(len(legend_entries(spec,v,style))/style['legend_columns']) for v in spec['views']]
    width=np*pw+(np-1)*gap+2*margin
    height=nv*ph+(nv-1)*gap+header+sum(legend_heights)+footer+0.25
    with plt.rc_context({'font.family':style['font_family'],'svg.fonttype':'path',
        'svg.hashsalt':'generate-plate-maps-v1','pdf.fonttype':42,'text.usetex':False}):
        fig=plt.figure(figsize=(width,height),dpi=style['dpi'],facecolor='#F7F9FC')
        fig.text(margin/width,1-0.25/height,spec['experiment']['title'],va='top',
            fontsize=style['title_size'],fontweight='bold',color='#1F3043')
        fig.text(margin/width,1-0.65/height,spec['experiment'].get('subtitle',''),
            va='top',fontsize=11,color='#526170')
        constraints=[]
        for ri,v in enumerate(spec['views']):
            top=height-header-ri*(ph+gap)
            for ci,p in enumerate(spec['plates']):
                left=margin+ci*(pw+gap)
                ax=fig.add_axes([left/width,(top-ph)/height,pw/width,ph/height])
                panel_rows,panel_cols=(len(p['rows']),p['columns']) if style['plate_scale']=='fit' else (mr,mc)
                constraints.extend(render_panel(ax,p,v,style,panel_rows,panel_cols))
        cursor=footer+sum(legend_heights)
        legends=[]
        for v,lh in zip(spec['views'],legend_heights):
            ax=fig.add_axes([margin/width,(cursor-lh)/height,(width-2*margin)/width,lh/height])
            ax.axis('off')
            handles=[Patch(facecolor=color,edgecolor=style['border_color'],label=label) for label,color in legend_entries(spec,v,style)]
            lg=ax.legend(handles=handles,title=v.get('legend',{}).get('title',v['name']),
                ncol=style['legend_columns'],loc='upper left',borderaxespad=0,frameon=False,
                fontsize=style['legend_font_size'],title_fontsize=style['legend_font_size']+0.5,
                handlelength=1.2,columnspacing=1.6,labelspacing=0.6,alignment='left')
            lg.get_title().set_fontweight('bold')
            legends.append((lg,ax))
            cursor-=lh
        fig.text(margin/width,0.17/height,spec['experiment'].get('footer',''),fontsize=10,color='#526170',va='bottom')
        fig.canvas.draw()
        renderer=fig.canvas.get_renderer()
        fitted=0
        for artist,bounds,shrink in constraints:
            x0,y0,x1,y1=bounds
            p0,p1=artist.axes.transData.transform((x0,y0)),artist.axes.transData.transform((x1,y1))
            aw,ah=abs(p1[0]-p0[0]),abs(p1[1]-p0[1])
            while True:
                box=artist.get_window_extent(renderer)
                if box.width<=aw and box.height<=ah:
                    break
                new_size=round(artist.get_fontsize()-0.2,2)
                if not shrink or new_size<style['min_font_size']:
                    plt.close(fig)
                    raise ValueError(f'Text does not fit: {artist.get_text()!r}. Increase cell_inches or shorten explicit label.')
                artist.set_fontsize(new_size)
                fitted+=1
        fig.canvas.draw()
        renderer=fig.canvas.get_renderer()
        for lg,ax in legends:
            box,available=lg.get_window_extent(renderer),ax.get_window_extent(renderer)
            require(box.x0>=available.x0-1 and box.x1<=available.x1+1 and box.y0>=available.y0-1,
                'Legend overflows: change legend_columns/font size or increase width')
        for artist in fig.texts:
            box=artist.get_window_extent(renderer)
            require(box.x0>=0 and box.x1<=fig.bbox.width and box.y0>=0 and box.y1<=fig.bbox.height,
                f'Global text overflows: {artist.get_text()!r}')
        fig._plate_map_fitting=dict(font_reduction_steps=fitted,text_fit_checked=True)
    return fig


def export(spec,prefix,formats=('png','svg','pdf')):
    spec=resolve_spec(spec)
    require(bool(formats) and len(formats)==len(set(formats)) and set(formats)<={'png','svg','pdf'},'Invalid export formats')
    fig=render(spec)
    style={**DEFAULT_STYLE,**spec.get('style',{})}
    prefix=Path(prefix).resolve()
    prefix.parent.mkdir(parents=True,exist_ok=True)
    def target(suffix):
        return Path(str(prefix)+suffix)
    normalized=json.dumps(spec,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    target('.json').write_text(normalized,encoding='utf-8')
    with plt.rc_context({'svg.fonttype':'path','svg.hashsalt':'generate-plate-maps-v1','pdf.fonttype':42}):
        for fmt in formats:
            metadata={'Creator':f'generate-plate-maps {VERSION}'}
            if fmt=='pdf':
                metadata.update(CreationDate=None,ModDate=None)
            if fmt=='svg':
                metadata['Date']=None
            # Complete each stream in memory before replacing a visible export.
            buffer=io.BytesIO()
            fig.savefig(buffer,format=fmt,dpi=style['dpi'],metadata=metadata)
            data=buffer.getvalue()
            if fmt=='png':
                require(data.endswith(b'\x00\x00\x00\x00IEND\xaeB\x60\x82'),'Incomplete PNG stream')
            temporary=target('.'+fmt+'.tmp')
            temporary.write_bytes(data)
            require(temporary.read_bytes()==data,f'Export write failed: {fmt}')
            temporary.replace(target('.'+fmt))
    attrs=sorted({k for p in spec['plates'] for w in p['wells'] for k in w['attributes']})
    with target('.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['plate_id','well','row','column','status','label']+['attribute.'+a for a in attrs]
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        for p in spec['plates']:
            order={r:i for i,r in enumerate(p['rows'])}
            for w in sorted(p['wells'],key=lambda w:(order[w['row']],w['column'])):
                writer.writerow(dict(plate_id=p['id'],well=f'{w["row"]}{w["column"]}',
                    **{k:w[k] for k in ('row','column','status')},label=w.get('label',style['empty_label']),
                    **{'attribute.'+k:v for k,v in w['attributes'].items()}))
    font=Path(font_manager.findfont(style['font_family'],fallback_to_default=False))
    report=dict(engine_version=VERSION,spec_sha256=hashlib.sha256(normalized.encode()).hexdigest(),
        renderer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        python=platform.python_version(),matplotlib=matplotlib.__version__,
        font_family=style['font_family'],font_sha256=hashlib.sha256(font.read_bytes()).hexdigest(),
        figure_inches=list(fig.get_size_inches()),dpi=style['dpi'],
        validation=dict(complete_wells=True,category_assignments=True,**fig._plate_map_fitting),
        plates=[dict(id=p['id'],status_counts=dict(Counter(w['status'] for w in p['wells'])),
            view_counts={v['id']:dict(Counter(w['attributes'][v['field']] if w['status']=='occupied' else 'empty' for w in p['wells'])) for v in spec['views']}) for p in spec['plates']])
    plt.close(fig)
    target('.manifest.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec',type=Path)
    parser.add_argument('--output-prefix',type=Path,required=True)
    parser.add_argument('--formats',nargs='+',default=['png','svg','pdf'])
    args=parser.parse_args()
    print(json.dumps(export(json.loads(args.spec.read_text()),args.output_prefix,args.formats),indent=2))

if __name__=='__main__':
    main()
