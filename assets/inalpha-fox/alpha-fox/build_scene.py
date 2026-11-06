"""Native Alpha Fox geometry traced from the approved colour mark; no raster assets."""
from pathlib import Path
import math
import xml.etree.ElementTree as E
from geometry import path, ellipse, group
INK='FF332317'; CREAM='FFFAF7EF'; RED='FFD54127'
root=E.Element('Rive',version='1',kind='fragment')
a=E.SubElement(root,'Artboard',name='Inalpha Alpha Fox',width='600',height='500',id='0:2',styleId='0:5',defaultStateMachineId='0:7',viewModelId='0:100',viewModelInstanceId='0:102')
E.SubElement(a,'LayoutComponentStyle',id='0:5')
art=E.SubElement(a,'Node',name='Approved alpha silhouette',x='-620',y='-280',scaleX='1.8',scaleY='1.8')
art=group(art,'Idle posture',(480,404),60)
art=group(art,'Click response',(480,404),61)
# Front-to-back: eyes and marks, ivory inset, alpha body, articulated tail.
face=E.SubElement(art,'Node',name='Face parallax',id='0:40')
expression=group(face,'Soft expression',(462,298),64)
gaze=E.SubElement(expression,'Node',name='Gaze offset',id='0:41')
for target,parent,id in [(42,face,44),(43,gaze,45)]:
 E.SubElement(parent,'TranslationConstraint',name='Gentle attention',targetId=f'0:{target}',id=f'0:{id}',sourceSpaceValue='local',destSpaceValue='local',strength='0')
E.SubElement(art,'Node',name='Face look target',id='0:42')
E.SubElement(art,'Node',name='Eye look target',id='0:43')
for x,i in [(442,20),(482,21)]:
 eye=group(gaze,'Left eye' if i==20 else 'Right eye',(x,298),i)
 # A tiny opening on the upper right edge, not a white eyeball or highlight.
 path(eye,'Abstract eye with negative-space notch',f'M {x} 281 C {x+3} 281 {x+5} 282 {x+6} 283 C {x+3} 286 {x+4} 290 {x+9} 290 C {x+11} 295 {x+11} 304 {x+7} 310 C {x+4} 316 {x-5} 316 {x-8} 310 C {x-12} 303 {x-11} 291 {x-7} 285 C {x-5} 282 {x-2} 281 {x} 281 Z',INK)
path(face,'Ivory face','M 462 248 C 490 247 509 271 510 303 C 510 337 491 366 466 371 C 439 375 417 358 414 328 C 409 294 429 250 462 248 Z',CREAM)
for x,y in [(456,225),(467,227),(478,225)]:
 path(art,'Forehead stroke',f'M {x} {y} L {x-1} {y+9}',CREAM,3)
path(art,'Left ear inset','M 427 220 L 434 208 L 443 223 Z',CREAM)
path(art,'Right ear inset','M 492 221 L 502 209 L 506 225 Z',CREAM)
path(art,'Alpha body and ears','M 420 233 C 422 218 427 200 432 190 C 434 186 437 188 441 193 L 461 216 C 469 215 478 216 483 218 L 504 190 C 507 186 510 187 512 193 L 526 239 C 544 256 552 277 557 300 C 561 321 562 350 568 368 C 574 386 584 393 593 397 C 580 403 559 405 543 399 C 526 393 520 376 515 362 C 498 389 475 403 449 404 C 411 408 381 392 373 363 C 361 323 378 268 401 247 C 407 241 414 236 420 233 Z',INK)
tail_response=group(art,'Responsive tail',(550,318),65)
tail=group(tail_response,'Tail root',(550,318),11)
path(tail,'Vermilion tail tip','M 548 307 C 567 278 598 244 644 252 C 628 268 625 292 612 312 C 591 319 574 309 548 307 Z',RED)
path(tail,'Tail dark base','M 548 307 C 567 278 598 244 644 252 C 628 268 625 292 612 312 C 600 333 586 345 576 348 C 568 337 563 324 559 314 L 548 307 Z',INK)
# Transparent hit plane: separate from art so the runtime remains background-free.
hit=path(a,'Pointer area','M 0 0 L 600 0 L 600 500 L 0 500 Z','00000000');hit.set('id','0:30')
back=path(a,'Optional presentation background','M 0 0 L 600 0 L 600 500 L 0 500 Z',CREAM)
back.set('opacity','0')
E.SubElement(back,'DataBindContext',sourcePathIds='0:100-0:103',propertyKey='18')
E.SubElement(a,'Node',name='Pointer handle',x='300',y='250',id='0:31')
E.SubElement(a,'Joystick',name='Bounded gaze',posX='300',posY='250',width='600',height='500',handleSourceId='0:31',xId='0:70',yId='0:71',id='0:32')
sm=E.SubElement(a,'StateMachine',name='Alpha Fox',id='0:7')
for typ in ['enter','move']:
 listener=E.SubElement(sm,'StateMachineListenerSingle',name='Follow pointer '+typ,targetId='0:30',listenerTypeValue=typ)
 E.SubElement(listener,'ListenerAlignTarget',targetId='0:31',preserveOffset='false')
layer=E.SubElement(sm,'StateMachineLayer',name='Natural idle')
E.SubElement(layer,'AnyState',x='0',y='-130');E.SubElement(layer,'ExitState',x='240',y='-130')
en=E.SubElement(layer,'EntryState',x='0',y='0');E.SubElement(en,'StateTransition',stateToId='0:9')
E.SubElement(layer,'AnimationState',x='240',y='0',animationId='0:6',id='0:9')
def anim(name,id,duration=480,loop='loop'):
 return E.SubElement(a,'LinearAnimation',name=name,duration=str(duration),fps='60',loopValue=loop,id=f'0:{id}')
def key(an,i,k,vals):
 o=E.SubElement(an,'KeyedObject',objectId=f'0:{i}');p=E.SubElement(o,'KeyedProperty',propertyKey=str(k))
 for f,v in vals:E.SubElement(p,'KeyFrameDouble',frame=str(f),value=f'{v:.7f}',interpolationType='linear')
an=anim('Idle - breathe, glance and double blink',6,720)
# Fast close, a short hold, slightly slower reopening. Unequal intervals.
blink=[(0,1),(136,1),(140,.45),(143,.075),(145,.075),(150,.7),(153,1),(347,1),(351,.45),(354,.075),(356,.075),(361,.7),(365,1),(373,1),(377,.075),(380,.075),(388,1),(550,1),(554,.075),(557,.075),(566,1),(720,1)]
for i in [20,21]:key(an,i,17,blink)
def wag(f):
 # Two short curious swishes, with quieter motion in between.
 base=.035*math.sin(2*math.pi*f/240)
 for start,end in [(205,325),(465,585)]:
  if start < f < end:
   t=(f-start)/(end-start)
   base += .085*math.sin(math.pi*t)**2*math.sin(4*math.pi*t)
 return base
key(an,11,15,[(f,wag(f)) for f in range(0,721,4)])
key(an,60,14,[(f,404-1.6*(1-math.cos(2*math.pi*f/240))/2) for f in range(0,721,4)])
def eased(an,i,k,vals):
 key(an,i,k,vals)
 for frame in list(an)[-1].iter('KeyFrameDouble'):
  frame.set('interpolationType','cubic')
  E.SubElement(frame,'CubicEaseInterpolator',x1='.42',y1='0',x2='.58',y2='1')
eased(an,60,15,[(0,0),(190,0),(225,-.027),(272,-.027),(318,0),(460,0),(490,.02),(535,.02),(580,0),(720,0)])
x=anim('Look horizontal',70,100,'oneShot');y=anim('Look vertical',71,100,'oneShot')
# Two small offsets add depth without distorting the alpha silhouette.
key(x,43,13,[(0,-4),(50,0),(100,4)]);key(x,42,13,[(0,-1.5),(50,0),(100,1.5)])
key(y,43,14,[(0,-3),(50,0),(100,3)]);key(y,42,14,[(0,-1),(50,0),(100,1)])

# Hover blend gently restores neutral gaze on pointer exit. No runtime scripts.
vm=E.SubElement(root,'ViewModel',name='AlphaFox',id='0:100',defaultInstanceId='0:102')
E.SubElement(vm,'ViewModelPropertyBoolean',name='isHovering',id='0:101')
E.SubElement(vm,'ViewModelPropertyNumber',name='backgroundOpacity',id='0:103')
inst=E.SubElement(vm,'ViewModelInstance',name='Default',id='0:102',exports='true')
E.SubElement(inst,'ViewModelInstanceBoolean',viewModelPropertyId='0:101',propertyValue='false')
E.SubElement(inst,'ViewModelInstanceNumber',viewModelPropertyId='0:103',propertyValue='0')
for typ,val in [('enter','true'),('move','true'),('exit','false')]:
 listener=E.SubElement(sm,'StateMachineListenerSingle',targetId='0:30',listenerTypeValue=typ,name='Attention '+typ)
 act=E.SubElement(listener,'ListenerViewModelChange')
 prop=E.SubElement(act,'BindablePropertyBoolean',propertyValue=val)
 E.SubElement(prop,'DataBindContext',sourcePathIds='0:100-0:101',propertyKey='634',direction='true')
attention=E.SubElement(sm,'StateMachineLayer',name='Attention and return')
E.SubElement(attention,'AnyState',x='0',y='-140');E.SubElement(attention,'ExitState',x='260',y='-140')
ent=E.SubElement(attention,'EntryState',x='0',y='0');E.SubElement(ent,'StateTransition',stateToId='0:80')
for id,ani,dest,val,xpos in [(80,82,81,'true',220),(81,83,80,'false',480)]:
 state=E.SubElement(attention,'AnimationState',id=f'0:{id}',animationId=f'0:{ani}',x=str(xpos),y='0')
 trans=E.SubElement(state,'StateTransition',stateToId=f'0:{dest}',duration='220')
 cond=E.SubElement(trans,'TransitionViewModelCondition',opValue='equal')
 comp=E.SubElement(cond,'TransitionPropertyViewModelComparator')
 prop=E.SubElement(comp,'BindablePropertyBoolean')
 E.SubElement(prop,'DataBindContext',sourcePathIds='0:100-0:101',propertyKey='634')
 E.SubElement(cond,'TransitionValueBooleanComparator',value=val)
for id,value in [(82,0),(83,1)]:
 pose=anim('Neutral gaze' if value==0 else 'Attentive gaze',id,1,'oneShot')
 for i in [44,45]:key(pose,i,172,[(0,value),(1,value)])

# One-shot click response, isolated from idle so motion never fights for a property.
E.SubElement(vm,'ViewModelPropertyTrigger',name='delight',id='0:104')
E.SubElement(inst,'ViewModelInstanceTrigger',viewModelPropertyId='0:104',propertyValue='0')
listener=E.SubElement(sm,'StateMachineListenerSingle',targetId='0:30',listenerTypeValue='click',name='Click to greet')
action=E.SubElement(listener,'ListenerViewModelChange')
prop=E.SubElement(action,'BindablePropertyTrigger',propertyValue='1')
E.SubElement(prop,'DataBindContext',sourcePathIds='0:100-0:104',propertyKey='686',direction='true')
reaction=E.SubElement(sm,'StateMachineLayer',name='Click greeting')
E.SubElement(reaction,'AnyState',x='0',y='-140');E.SubElement(reaction,'ExitState',x='250',y='-140')
entry=E.SubElement(reaction,'EntryState',x='0',y='0');E.SubElement(entry,'StateTransition',stateToId='0:90')
rest=E.SubElement(reaction,'AnimationState',id='0:90',animationId='0:92',x='230',y='0')
trans=E.SubElement(rest,'StateTransition',stateToId='0:91',duration='70')
cond=E.SubElement(trans,'TransitionViewModelCondition',opValue='equal')
comp=E.SubElement(cond,'TransitionPropertyViewModelComparator')
prop=E.SubElement(comp,'BindablePropertyTrigger')
E.SubElement(prop,'DataBindContext',sourcePathIds='0:100-0:104',propertyKey='686')
E.SubElement(cond,'TransitionValueTriggerComparator',value='1')
play=E.SubElement(reaction,'AnimationState',id='0:91',animationId='0:93',x='480',y='0',reset='true')
E.SubElement(play,'StateTransition',stateToId='0:90',duration='120',enableExitTime='true',exitTimeIsPercetange='true',exitTime='100')
rest_an=anim('Greeting ready',92,1,'oneShot')
for i,k,v in [(61,14,404),(61,15,0),(61,16,1),(61,17,1),(64,17,1),(65,15,0)]:key(rest_an,i,k,[(0,v),(1,v)])
greet=anim('Greeting - anticipation, lift and tail flourish',93,84,'oneShot')
eased(greet,61,14,[(0,404),(9,405),(22,398),(38,404),(49,402.5),(62,404),(84,404)])
eased(greet,61,15,[(0,0),(9,-.015),(22,.018),(40,-.008),(62,0),(84,0)])
eased(greet,61,16,[(0,1),(9,1.012),(22,.995),(40,1.008),(62,1),(84,1)])
eased(greet,61,17,[(0,1),(9,.987),(22,1.008),(40,.995),(62,1),(84,1)])
eased(greet,64,17,[(0,1),(9,.85),(22,1),(38,.68),(50,.68),(62,1),(84,1)])
eased(greet,65,15,[(0,0),(10,-.06),(22,.10),(34,-.07),(46,.045),(60,-.015),(72,0),(84,0)])
E.indent(root,space='  ')
Path(__file__).with_name('scene.rml').write_text(E.tostring(root,encoding='unicode')+'\n')
