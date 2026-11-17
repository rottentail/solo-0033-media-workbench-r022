"""Inverse resampling and edge-weighted composition in scene-linear sRGB."""
import numpy as np


def linear(values):
    values=np.asarray(values,dtype=float)/255
    return np.where(values<=0.04045,values/12.92,((values+0.055)/1.055)**2.4)


def encoded(values):
    values=np.clip(values,0,1)
    code=np.where(values<=0.0031308,12.92*values,1.055*values**(1/2.4)-0.055)
    return np.rint(code*255).astype('uint8')


def sample(frame, mapping):
    x,y,valid,edge=mapping
    x0,y0=np.floor(x).astype(int),np.floor(y).astype(int)
    x1,y1=np.minimum(x0+1,frame.shape[1]-1),np.minimum(y0+1,frame.shape[0]-1)
    dx,dy=(x-x0)[...,None],(y-y0)[...,None]
    rgb=linear(frame)
    value=((rgb[y0,x0]*(1-dx)+rgb[y0,x1]*dx)*(1-dy)
           +(rgb[y1,x0]*(1-dx)+rgb[y1,x1]*dx)*dy)
    return value, (valid*edge)[...,None]


def compose(left, right, maps):
    a,wa=sample(left,maps[0])
    b,wb=sample(right,maps[1])
    return encoded((a*wa+b*wb)/(wa+wb))
