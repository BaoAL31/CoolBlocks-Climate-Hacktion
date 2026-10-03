import numpy as np, solweig, glob, os, math, rasterio, logging, warnings
from datetime import datetime, timedelta
logging.disable(logging.CRITICAL); warnings.filterwarnings("ignore")
LAT,LON=-33.815,151.003  # Parramatta
def noaa(dt_local, utc):
    # independent NOAA general solar position approximation
    t=dt_local-timedelta(hours=utc); doy=t.timetuple().tm_yday
    g=2*math.pi/365*(doy-1+(t.hour-12)/24+t.minute/1440)
    eot=229.18*(0.000075+0.001868*math.cos(g)-0.032077*math.sin(g)-0.014615*math.cos(2*g)-0.040849*math.sin(2*g))
    dec=0.006918-0.399912*math.cos(g)+0.070257*math.sin(g)-0.006758*math.cos(2*g)+0.000907*math.sin(2*g)-0.002697*math.cos(3*g)+0.00148*math.sin(3*g)
    tst=t.hour*60+t.minute+eot+4*LON
    ha=math.radians(tst/4-180); la=math.radians(LAT)
    cz=math.sin(la)*math.sin(dec)+math.cos(la)*math.cos(dec)*math.cos(ha); z=math.acos(cz)
    az=math.degrees(math.atan2(math.sin(ha), math.cos(ha)*math.sin(la)-math.tan(dec)*math.cos(la)))+180
    return 90-math.degrees(z), az%360
print("== A. Sun position: SOLWEIG vs independent NOAA formula (Parramatta)")
cases=[(datetime(2026,1,15,9,30),11),(datetime(2026,1,15,13,30),11),(datetime(2026,1,15,16,30),11),(datetime(2026,6,21,12,30),10),(datetime(2026,6,21,8,30),10)]
for dt,u in cases:
    loc=solweig.Location(latitude=LAT,longitude=LON,utc_offset=u)
    w=solweig.Weather(datetime=dt,ta=25,rh=40,global_rad=500); w.compute_derived(loc)
    a,z=noaa(dt-timedelta(minutes=30),u)
    print(f"  {dt-timedelta(minutes=30):%d %b %H:%M} UTC+{u}: SOLWEIG alt {w.sun_altitude:5.1f} az {w.sun_azimuth:5.1f} | NOAA alt {a:5.1f} az {z:5.1f}")

print("== B. Shadow direction/length: 20 m pole (2x2 m) on flat ground, 1 m grid, north = top row")
def shadow_of(dt,u):
    dsm=np.zeros((301,301),np.float32); dsm[149:151,149:151]=20
    s=solweig.SurfaceData.prepare(dsm=dsm,pixel_size=1.0)
    loc=solweig.Location(latitude=LAT,longitude=LON,utc_offset=u)
    w=solweig.Weather(datetime=dt,ta=25,rh=40,global_rad=600)
    od=f"o_{dt:%m%d%H}"; solweig.calculate(s,weather=[w],location=loc,output_dir=od,outputs=["shadow"])
    f=sorted(glob.glob(f"{od}/**/*shadow*.tif",recursive=True))[0]
    sh=rasterio.open(f).read(1); m=(sh<0.5); m[149:151,149:151]=False
    r,c=np.nonzero(m); dy=-(r.mean()-150); dx=c.mean()-150   # +dy = north, +dx = east
    L=max(np.hypot(r-150,c-150)); w.compute_derived(loc)
    exp=20/math.tan(math.radians(w.sun_altitude)); expaz=(w.sun_azimuth+180)%360
    print(f"  {dt-timedelta(minutes=30):%d %b %H:%M}: shadow points to {math.degrees(math.atan2(dx,dy))%360:5.0f}° (expected {expaz:5.0f}°), length {L:5.1f} m (expected {exp:5.1f} m)")
shadow_of(datetime(2026,6,21,12,30),10); shadow_of(datetime(2026,1,15,9,30),11); shadow_of(datetime(2026,1,15,17,30),11)

print("== C. Western Sydney heatwave street: E-W street, 10 m buildings, a row of 8 m trees on the south footpath")
H,W=120,120
dsm=np.zeros((H,W),np.float32); dsm[:45,:]=10; dsm[75:,:]=10      # street rows 45..74 (30 m wide)
cdsm=np.zeros_like(dsm); 
for c in range(10,110,12): cdsm[64:71,c:c+7]=8                    # tree crowns
s_base=solweig.SurfaceData.prepare(dsm=dsm,pixel_size=1.0,working_dir="wb")
s_tree=solweig.SurfaceData.prepare(dsm=dsm,cdsm=cdsm,pixel_size=1.0,working_dir="wt")
loc=solweig.Location(latitude=LAT,longitude=LON,utc_offset=11)
def ghi(dt):
    w=solweig.Weather(datetime=dt,ta=30,rh=30,global_rad=0); w.compute_derived(loc)
    cz=max(math.sin(math.radians(w.sun_altitude)),0); return 1098*cz*math.exp(-0.057/cz) if cz>0.01 else 0
ta={9:31,10:33,11:35,12:37,13:39,14:41,15:42,16:42,17:41,18:39}
ws=[solweig.Weather(datetime=datetime(2026,1,15,h,0),ta=t,rh=max(12,35-(t-31)*2),global_rad=ghi(datetime(2026,1,15,h,0)),ws=2.0) for h,t in ta.items()]
rb=solweig.calculate(s_base,weather=ws,location=loc,output_dir="ob",outputs=["tmrt","utci"])
rt=solweig.calculate(s_tree,weather=ws,location=loc,output_dir="ot",outputs=["tmrt","utci"])
def at(od,var,h):
    f=[x for x in sorted(glob.glob(f"{od}/**/*{var}*.tif",recursive=True)) if f"{h:02d}00" in os.path.basename(x)]
    return rasterio.open(f[0]).read(1) if f else None
print("  files e.g.:",sorted(os.path.basename(x) for x in glob.glob("ot/**/*.tif",recursive=True))[:3])
for h in (12,15):
    tb,ub,tt,ut=at("ob","tmrt",h),at("ob","utci",h),at("ot","tmrt",h),at("ot","utci",h)
    if tb is None: print("  missing",h); continue
    sunlit=(slice(55,60),slice(40,80)); under=(slice(66,69),slice(13,15)); 
    print(f"  {h}:00 Ta={ta[h]}°C  open street (no trees):  Tmrt {np.nanmean(tb[sunlit]):5.1f}  UTCI {np.nanmean(ub[sunlit]):5.1f}")
    print(f"         under a tree:  Tmrt {np.nanmean(tt[under]):5.1f} (was {np.nanmean(tb[under]):5.1f})  UTCI {np.nanmean(ut[under]):5.1f} (was {np.nanmean(ub[under]):5.1f})")
    d=ut-ub; prof=[np.nanmean(d[r,10:110]) for r in range(46,74,3)]
    print("         UTCI change by row across street (N→S):",' '.join(f"{v:+.1f}" for v in prof))
