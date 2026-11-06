import math, re
import xml.etree.ElementTree as E

def group(parent,name,pivot,id=None):
    attrs=dict(name=name,x=str(pivot[0]),y=str(pivot[1]))
    if id: attrs['id']=f'0:{id}'
    n=E.SubElement(parent,'Node',attrs)
    return E.SubElement(n,'Node',name=f'{name} local',x=str(-pivot[0]),y=str(-pivot[1]))

def path(parent,name,d,color,stroke=0):
    """Translate a small absolute M/L/C/Z path into native cubic vertices."""
    tokens=re.findall(r'[MLCZ]|-?\d*\.?\d+',d)
    vertices=[]; i=0; closed=False
    while i<len(tokens):
        cmd=tokens[i];i+=1
        if cmd in ['M','L']:
            x,y=map(float,tokens[i:i+2]);i+=2
            vertices.append([x,y,x,y,x,y])
        elif cmd=='C':
            cx,cy,dx,dy,x,y=map(float,tokens[i:i+6]);i+=6
            vertices[-1][4:]=[cx,cy]
            if vertices and x==vertices[0][0] and y==vertices[0][1]:
                vertices[0][2:4]=[dx,dy]
            else: vertices.append([x,y,dx,dy,x,y])
        elif cmd=='Z':closed=True
        else: raise ValueError(cmd)
    s=E.SubElement(parent,'Shape',name=name)
    p=E.SubElement(s,'PointsPath',isClosed=str(closed).lower())
    for x,y,ix,iy,ox,oy in vertices:
        E.SubElement(p,'CubicDetachedVertex',x=str(x),y=str(y),inRotation=str(math.atan2(iy-y,ix-x)),inDistance=str(math.hypot(ix-x,iy-y)),outRotation=str(math.atan2(oy-y,ox-x)),outDistance=str(math.hypot(ox-x,oy-y)))
    paint=E.SubElement(s,'Stroke',thickness=str(stroke),cap='round',join='round') if stroke else E.SubElement(s,'Fill')
    E.SubElement(paint,'SolidColor',colorValue=color)
    return s

def ellipse(parent,name,x,y,w,h,color):
    s=E.SubElement(parent,'Shape',name=name,x=str(x),y=str(y))
    E.SubElement(s,'Ellipse',width=str(w),height=str(h))
    f=E.SubElement(s,'Fill');E.SubElement(f,'SolidColor',colorValue=color)
