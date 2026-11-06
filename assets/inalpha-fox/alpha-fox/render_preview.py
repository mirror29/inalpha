"""Capture the real Rive state machine; assemble unmodified rendered frames into GIF."""
from pathlib import Path
import math, subprocess
from PIL import Image, ImageChops
P=Path(__file__).resolve().parent
R=str(Path.home()/'.rive/bin/rive')
B=P/'build'; B.mkdir(exist_ok=True)
def render(name,*args):
 p=B/(name+'.png')
 subprocess.run([R,str(P),'--data=backgroundOpacity=1',f'--screenshot={p}',*args],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 return p
# Capture verification poses at equal elapsed time.
for name,args in {
 'neutral':['--advance=30'],
 'left':['--advance=1','--pointer=move@20,100','--advance=29'],
 'right':['--advance=1','--pointer=move@580,400','--advance=29'],
 'return':['--advance=1','--pointer=move@580,400','--advance=14','--pointer=exit@610,400','--advance=15'],
 'closed':['--advance=144'],
 'tail-a':['--advance=60'],
 'tail-b':['--advance=180'],
 'loop-start':['--advance=1'],
 'loop-end':['--advance=721'],
}.items():render(name,*args)
# Read pixels only: gaze must move, return must settle, and the loop must match.
def eye_center(name):
 im=Image.open(B/(name+'.png')).convert('RGB')
 pts=[(x,y) for y in range(215,295) for x in range(140,280) if max(im.getpixel((x,y)))<100]
 return tuple(sum(q[i] for q in pts)/len(pts) for i in [0,1])
c={name:eye_center(name) for name in ['neutral','left','right','return','closed']}
assert c['right'][0]-c['left'][0]>10,c
assert c['right'][1]-c['left'][1]>5,c
assert math.dist(c['neutral'],c['return'])<.2,c
# Float timeline accumulation can shift edge antialiasing by 1–2/255.
diff=ImageChops.difference(Image.open(B/'loop-start.png').convert('RGB'),Image.open(B/'loop-end.png').convert('RGB'))
assert max(max(pixel) for pixel in diff.getdata()) <= 2, 'Loop seam intensity'
assert sum(any(pixel) for pixel in diff.getdata()) < 100, 'Loop seam coverage'
print('Verified native pointer left/right, pointer-exit return, and seamless loop:',c,flush=True)
for tag,events in {
 'click-control':['--advance=1','--pointer=move@300,250','--advance=22'],
 'click-response':['--advance=1','--pointer=click@300,250','--advance=22'],
 'repeat-control':['--advance=1','--pointer=click@300,250','--advance=120','--pointer=move@300,250','--advance=22'],
 'repeat-response':['--advance=1','--pointer=click@300,250','--advance=120','--pointer=click@300,250','--advance=22'],
}.items():render(tag,*events)
for prefix in ['click','repeat']:
 diff=ImageChops.difference(Image.open(B/(prefix+'-control.png')).convert('RGB'),Image.open(B/(prefix+'-response.png')).convert('RGB'))
 assert sum(any(pixel) for pixel in diff.getdata()) > 1000, prefix+' did not animate'
print('Click and repeated-click responses verified.',flush=True)
frames=[]
for f in range(0,720,4):
 # A smooth pointer orbit demonstrates the actual listeners plus autonomous idle.
 x=300+230*math.sin(2*math.pi*f/720)
 y=250-170*math.cos(2*math.pi*f/720)
 args=['--advance=1','--pointer=move@300,250']
 if f>=420:
  args += ['--advance=419','--pointer=click@300,250']
  if f>420:args += [f'--advance={f-420}']
 elif f>1:args += [f'--advance={f-1}']
 args += [f'--pointer=move@{x:.2f},{y:.2f}','--advance=1']
 p=render(f'preview-{f:03}',*args)
 with Image.open(p) as im:frames.append(im.convert('RGB'))
frames[0].save(B/'inalpha-alpha-fox-lively.gif',save_all=True,append_images=frames[1:],duration=[70,60,70]*60,loop=0)
print(B/'inalpha-alpha-fox-lively.gif')
