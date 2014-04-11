import numpy, itertools,scipy
try:
    from _convolution import gaussian_filter

except ImportError:
    from scipy.ndimage.filters import gaussian_filter
from math import sqrt

from utils import binning, timeit

def image_test():
    img = numpy.zeros((128*4,128*4))
    a = numpy.linspace(0.5, 8, 16)
    xc = [64,64,64,64,192,192,192,192,320,320,320,320,448,448,448,448]
    yc = [64,192,320,448,64,192,320,448,64,192,320,448,64,192,320,448]
    cpt = 0
    for sigma in a:
        img = make_gaussian(img,sigma,xc[cpt],yc[cpt])
        cpt = cpt + 1
    return img

def make_gaussian(im,sigma,xc,yc):
    size =int( 8*sigma +1 )
    if size%2 == 0 :
           size += 1
    x = numpy.arange(0, size, 1, float)
    y = x[:,numpy.newaxis]
    x0 = y0 = size // 2
    gaus = numpy.exp(-4*numpy.log(2) * ((x-x0)**2 + (y-y0)**2) / sigma**2)
    im[xc-size/2:xc+size/2+1,yc-size/2:yc+size/2+1] = gaus
    return im

@timeit
def local_max_min(img,prev_dog, cur_dog, next_dog, sigma, mask=None, n_5=True ):
    """
    @param prev_dog, cur_dog, next_dog: 3 subsequent Difference of gaussian
    @param sigma: value of sigma for cur_dog 
    @parm mask: mask out keypoint next to the mask (or inside the mask)
    """

    kpm = numpy.zeros(shape=cur_dog.shape, dtype=numpy.uint8)
    slic = cur_dog[1:-1, 1:-1] 
    kpm[1:-1, 1:-1] += (slic > cur_dog[:-2, 1:-1]) * (slic > cur_dog[2:, 1:-1])
    kpm[1:-1, 1:-1] += (slic > cur_dog[1:-1, :-2]) * (slic > cur_dog[1:-1, 2:])
    kpm[1:-1, 1:-1] += (slic > cur_dog[:-2, :-2]) * (slic > cur_dog[2:, 2:])
    kpm[1:-1, 1:-1] += (slic > cur_dog[2:, :-2]) * (slic > cur_dog[:-2, 2:])

    #with next DoG
    kpm[1:-1, 1:-1] += (slic > next_dog[:-2, 1:-1]) * (slic > next_dog[2:, 1:-1])
    kpm[1:-1, 1:-1] += (slic > next_dog[1:-1, :-2]) * (slic > next_dog[1:-1, 2:])
    kpm[1:-1, 1:-1] += (slic > next_dog[:-2, :-2]) * (slic > next_dog[2:, 2:])
    kpm[1:-1, 1:-1] += (slic > next_dog[2:, :-2]) * (slic > next_dog[:-2, 2:])
    kpm[1:-1, 1:-1] += (slic >= next_dog[1:-1, 1:-1])

    #with previous DoG
    kpm[1:-1, 1:-1] += (slic > prev_dog[:-2, 1:-1]) * (slic > prev_dog[2:, 1:-1])
    kpm[1:-1, 1:-1] += (slic > prev_dog[1:-1, :-2]) * (slic > prev_dog[1:-1, 2:])
    kpm[1:-1, 1:-1] += (slic > prev_dog[:-2, :-2]) * (slic > prev_dog[2:, 2:])
    kpm[1:-1, 1:-1] += (slic > prev_dog[2:, :-2]) * (slic > prev_dog[:-2, 2:])
    kpm[1:-1, 1:-1] += (slic >= prev_dog[1:-1, 1:-1])
    
    
    if n_5:
        target = 38
        slic = cur_dog[2:-2,2:-2]
        
        kpm[2:-2,2:-2] += (slic > cur_dog[:-4, 2:-2]) * (slic > cur_dog[4:, 2:-2]) #decalage horizontal
        kpm[2:-2, 2:-2] += (slic > cur_dog[2:-2, :-4]) * (slic > cur_dog[2:-2, 4:]) #decalage vertical
        kpm[2:-2, 2:-2] += (slic > cur_dog[:-4, :-4]) * (slic > cur_dog[4:, 4:])   #diagonale 
        kpm[2:-2, 2:-2] += (slic > cur_dog[4:, :-4]) * (slic > cur_dog[:-4, 4:])    
        kpm[2:-2, 2:-2] += (slic > cur_dog[4:, 1:-3]) * (slic > cur_dog[:-4, 1:-3])
        kpm[2:-2, 2:-2] += (slic > cur_dog[1:-3, :-4]) * (slic > cur_dog[1:-3, 4:])
        kpm[2:-2, 2:-2] += (slic > cur_dog[3:-1, :-4]) * (slic > cur_dog[3:-1, 4:])
        kpm[2:-2, 2:-2] += (slic > cur_dog[4:, 3:-1]) * (slic > cur_dog[:-4, 3:-1])
    
        #with next DoG
        kpm[2:-2, 2:-2] += (slic > next_dog[:-4, 2:-2]) * (slic > next_dog[4:, 2:-2])
        kpm[2:-2, 2:-2] += (slic > next_dog[2:-2, :-4]) * (slic > next_dog[2:-2, 4:])
        kpm[2:-2, 2:-2] += (slic > next_dog[:-4, :-4]) * (slic > next_dog[4:, 4:])
        kpm[2:-2, 2:-2] += (slic > next_dog[4:, :-4]) * (slic > next_dog[:-4, 4:])
        kpm[2:-2, 2:-2] += (slic > next_dog[4:, 1:-3]) * (slic > next_dog[:-4, 1:-3])
        kpm[2:-2, 2:-2] += (slic > next_dog[1:-3, :-4]) * (slic > next_dog[1:-3, 4:])
        kpm[2:-2, 2:-2] += (slic > next_dog[3:-1, :-4]) * (slic > next_dog[3:-1, 4:])
        kpm[2:-2, 2:-2] += (slic > next_dog[4:, 3:-1]) * (slic > next_dog[:-4, 3:-1])
    
        #with previous DoG
        kpm[2:-2, 2:-2] += (slic > prev_dog[:-4, 2:-2]) * (slic > prev_dog[4:, 2:-2])
        kpm[2:-2, 2:-2] += (slic > prev_dog[2:-2, :-4]) * (slic > prev_dog[2:-2, 4:])
        kpm[2:-2, 2:-2] += (slic > prev_dog[:-4, :-4]) * (slic > prev_dog[4:, 4:])
        kpm[2:-2, 2:-2] += (slic > prev_dog[4:, :-4]) * (slic > prev_dog[:-4, 4:])
        kpm[2:-2, 2:-2] += (slic > prev_dog[4:, 1:-3]) * (slic > prev_dog[:-4, 1:-3])
        kpm[2:-2, 2:-2] += (slic > prev_dog[1:-3, :-4]) * (slic > prev_dog[1:-3, 4:])
        kpm[2:-2, 2:-2] += (slic > prev_dog[3:-1, :-4]) * (slic > prev_dog[3:-1, 4:])
        kpm[2:-2, 2:-2] += (slic > prev_dog[4:, 3:-1]) * (slic > prev_dog[:-4, 3:-1])
                
    else: 
        target=14
           
    if mask is not None: # les points a 1 dans le masque ne seront pas pris en compte
        not_mask = numpy.logical_not(mask)
        valid_point = numpy.logical_and(not_mask, kpm >= target)
    else:
        valid_point = (kpm >= target)
            
    kpy, kpx = numpy.where(valid_point)
    l = kpx.size
    keypoints = numpy.empty((l,4),dtype=numpy.float32)
    keypoints[:, 0] = kpx
    keypoints[:, 1] = kpy
    keypoints[:, 2] = sigma
    keypoints[:, 3] = cur_dog[(kpy, kpx)]
    return keypoints


class BlobDetection(object):
    """
    
    """
    def __init__(self, img, cur_sigma=0.25, init_sigma = 0.5, dest_sigma = 8, scale_per_octave = 8):
        """
        Performs a blob detection:
        http://en.wikipedia.org/wiki/Blob_detection
        using a Difference of Gaussian + Pyramid of Gaussians
        
        @param img: input image
        @param cur_sigma: estimated smoothing of the input image. 0.25 correspond to no interaction between pixels.
        @param init_sigma: start searching at this scale (sigma=0.5: 10% interaction with first neighbor)
        @param dest_sigma: sigma at which the resolution is lowered (change of octave)
        @param scale_per_octave: Number of scale to be performed per octave
        """
        self.raw = numpy.ascontiguousarray(img, dtype=numpy.float32)
        self.cur_sigma = float(cur_sigma)
        self.init_sigma = float(init_sigma)
        self.dest_sigma = float(dest_sigma)
        self.scale_per_octave = int(scale_per_octave)
        self.data = None    # current image
        self.sigmas = None  # contains pairs of absolute sigma and relative ones...
        self.blurs = []     # different blurred images
        self.dogs = []      # different difference of gaussians
        self.border_size = 5# size of the border
        self.keypoints = []
        self.delta = []
#         self.mask = numpy.zeros(self.data.shape)
#         self.mask[0:100,0:100] = 1

    def _initial_blur(self):
        """
        Blur the original image to achieve the requested level of blur init_sigma
        """
        if self.init_sigma > self.cur_sigma:
            sigma = sqrt(self.init_sigma ** 2 - self.cur_sigma ** 2)
            self.data = gaussian_filter(self.raw, sigma)
        else:
            self.data = self.raw

    def _calc_sigma(self):
        """
        Calculate all sigma to blur an image within an octave
        """
        if not self.data:
            self._initial_blur()
        previous = self.init_sigma
        incr = 0
        self.sigmas = [(previous, incr)]
        for i in range(1, self.scale_per_octave + 3):
            sigma_abs = self.init_sigma * (self.dest_sigma / self.init_sigma) ** (1.0 * i / (self.scale_per_octave))
            increase = previous * sqrt((self.dest_sigma / self.init_sigma) ** (2.0 / self.scale_per_octave) - 1.0)
            self.sigmas.append((sigma_abs, increase))
            previous = sigma_abs

    @timeit
    def _one_octave(self):
        """
        Return the blob coordinates for an octave 
        """
        x=[]
        y=[]
        sigmas=[]
        
        if not self.sigmas:
            self._calc_sigma()
            
        previous = self.data

        for sigma_abs, sigma_rel in self.sigmas:
            if  sigma_rel == 0:
                self.blurs.append(previous)
            else:
                new_blur = gaussian_filter(previous, sigma_rel)
                self.blurs.append(new_blur)
                self.dogs.append(previous - new_blur)
                previous = new_blur
                
        for i in range(1, self.scale_per_octave + 1):
            sigma = self.sigmas[i][0]
            self.keypoints.append(local_max_min(img,self.dogs[i - 1], self.dogs[i], self.dogs[i + 1], sigma=sigma, n_5=False))
            kx = numpy.transpose(self.keypoints[i-1])[0]
            ky = numpy.transpose(self.keypoints[i-1])[1]
            kx,ky,sigma,dx,dy = self.refine_SG4(i,kx,ky,sigma)
            x.append(kx)
            
            y.append(ky)
            sigmas.append(sigma)
            print kx.__len__(), ky.__len__(),sigma.__len__()
            
        #shrink data so that
        self.data = binning(self.blurs[self.scale_per_octave], 2)
#         self.mask = binning(self.mask,2)
        return x,y,dx,dy,sigmas
    
  
    def refine_SG4(self,j,kx,ky,sigma):
        """ Savitzky Golay algorithm to check if a point is really the maximum """
        print j, sigma
        delta=[]
        deltax=[]
        deltay=[]
        k2x=[]
        k2y=[]
        sigmas=[]
        i=0
        cpt=0
        prev_dog = self.dogs[j-1]
        curr_dog = self.dogs[j]
        next_dog = self.dogs[j+1]
        
        #savitsky golay ordre 4 patch 5
        SGX0Y0 = [0.04163265,-0.08081633,0.07836735,-0.08081633,0.04163265,-0.08081633,-0.01959184,0.20081633,-0.01959184,-0.08081633,0.07836735,0.20081633,0.44163265,0.20081633,0.07836735,-0.08081633,-0.01959184,0.20081633,-0.01959184,-0.08081633,0.04163265,-0.08081633,0.07836735,-0.08081633,0.04163265]
        SGX1Y0 = [0.07380952,-0.10476190,0.00000000,0.10476190,-0.07380952,-0.01190476,-0.14761905,0.00000000,0.14761905,0.01190476,-0.04047619,-0.16190476,0.00000000,0.16190476,0.04047619,-0.01190476,-0.14761905,0.00000000,0.14761905,0.01190476,0.07380952,-0.10476190,0.00000000,0.10476190,-0.07380952]
        SGX2Y0 = [-0.04914966,0.15374150,-0.20918367,0.15374150,-0.04914966,0.01207483,0.12312925,-0.27040816,0.12312925,0.01207483,0.03248299,0.11292517,-0.29081633,0.11292517,0.03248299,0.01207483,0.12312925,-0.27040816,0.12312925,0.01207483,-0.04914966,0.15374150,-0.20918367,0.15374150,-0.04914966]
        SGX0Y1 = [0.07380952,-0.01190476,-0.04047619,-0.01190476,0.07380952,-0.10476190,-0.14761905,-0.16190476,-0.14761905,-0.10476190,0.00000000,0.00000000,0.00000000,0.00000000,0.00000000,0.10476190,0.14761905,0.16190476,0.14761905,0.10476190,-0.07380952,0.01190476,0.04047619,0.01190476,-0.07380952]
        SGX1Y1 = [-0.07333333,0.10500000,0.00000000,-0.10500000,0.07333333,0.10500000,0.12333333,0.00000000,-0.12333333,-0.10500000,0.00000000,0.00000000,0.00000000,0.00000000,0.00000000,-0.10500000,-0.12333333,0.00000000,0.12333333,0.10500000,0.07333333,-0.10500000,0.00000000,0.10500000,-0.07333333]
        SGX0Y2 = [-0.04914966,0.01207483,0.03248299,0.01207483,-0.04914966,0.15374150,0.12312925,0.11292517,0.12312925,0.15374150,-0.20918367,-0.27040816,-0.29081633,-0.27040816,-0.20918367,0.15374150,0.12312925,0.11292517,0.12312925,0.15374150,-0.04914966,0.01207483,0.03248299,0.01207483,-0.04914966]

        
        for y,x in itertools.izip(ky,kx):
            if (x > 1 and x < curr_dog.shape[1]-2 and y > 1 and y < curr_dog.shape[0]-2):
                patch5 = curr_dog[y-2:y+3,x-2:x+3]
                patch5_prev = prev_dog[y-2:y+3,x-2:x+3]
                patch5_next = next_dog[y-2:y+3,x-2:x+3]

#                 print curr_dog.shape[1]-3,x,y,patch5
                dx = (SGX1Y0*patch5.ravel()).sum()
                dy = (SGX0Y1*patch5.ravel()).sum()
                d2x = (SGX2Y0*patch5.ravel()).sum()
                d2y = (SGX0Y2*patch5.ravel()).sum()
                dxy = (SGX1Y1*patch5.ravel()).sum()

                s_next = (SGX0Y0*patch5_next.ravel()).sum()
                s = (SGX0Y0*patch5.ravel()).sum()
                s_prev = (SGX0Y0*patch5_prev.ravel()).sum()
                d2s = (s_next + s_prev - 2.0*s) /4.0
                ds = (s_next - s_prev) /2.0
                
                dx_next = (SGX1Y0*patch5_next.ravel()).sum()
                dx_prev = (SGX1Y0*patch5_prev.ravel()).sum()
                
                dy_next = (SGX0Y1*patch5_next.ravel()).sum()
                dy_prev = (SGX0Y1*patch5_prev.ravel()).sum()
                
                dxs = (dx_next - dx_prev)/2.0
                dys = (dy_next - dy_prev)/2.0                
                                
                lap = numpy.array([[d2y,dxy,dys],[dxy,d2x,dxs],[dys,dxs,d2s]])

                delta = (numpy.dot(numpy.linalg.inv(lap),[dy,dx,ds]))
                err = numpy.linalg.norm(delta[:-1])
           
                if  err < numpy.sqrt(2.0) and abs(delta[0]) < 1 and abs(delta[1]) < 1 :
                    k2x.append(x-delta[1])
                    k2y.append(y-delta[0])
                    sigmas.append(sigma-delta[2])
                    deltax.append(-delta[1])
                    deltay.append(-delta[0])
                    
        return k2x,k2y,sigmas,deltax,deltay
                      
    def Otsu(self,sigma):
        
            #building histogram with the corrected sigmas
        sigma = numpy.asarray(sigma)
        pylab.figure(2)
        pylab.clf()
        pylab.hist(sigma, bins = 1000)
        pylab.show()
         
        h = pylab.hist(sigma, bins = 1000)
        n = h[0].__len__()
        Proba = h[0]/float(numpy.sum(h[0]))
        
        max = 0.0
        
        for cpt in range( n ):
            Proba1 = Proba[: cpt]
            Proba2 = Proba[cpt :]        
            P1 = numpy.sum(Proba1)
            P2 = numpy.sum(Proba2)
            
            n1 = numpy.arange(cpt)
            n2 = numpy.arange(cpt,n)        
            Moy1 = sum( n1 * Proba1)/P1
            Moy2 = sum( n2 * Proba2)/P2
            
            VarInterC = P1*P2*(Moy1-Moy2)**2
            
            if VarInterC > max : 
                max = VarInterC
                index = cpt
               
        print 'sigma pour la separation'
        print h[1][index]
        return h[1][index]

if __name__ == "__main__":
    
    kx=[]
    ky=[]
    k2x=[]
    k2y=[]
    dx=[]
    dy=[]
    

    import fabio,pylab
#     img = fabio.open("../../testimages/LaB6_0003.mar3450").data
#     img = fabio.open("../../testimages/grid2k0000.edf").data
    img = fabio.open("../../testimages/halfccd.edf").data
#     img = numpy.log1p(img)
#     img = img[img.shape[0]/2-400:img.shape[0]/2+400,img.shape[1]/2-400:img.shape[1]/2+400]
#     img = image_test()

    bd = BlobDetection(img)
    kx,ky,dx,dy,sigma = bd._one_octave()
    sigma = numpy.concatenate(sigma)
    print bd.sigmas
    
#  building arrays x and y containing all the coordinates of the keypoints, only for vizualisation
    x=[]
    y=[]
    print bd.keypoints.__len__()
    for j in range( bd.keypoints.__len__() ):
        k = bd.keypoints[j]
        x.extend(numpy.transpose(k)[0])
        y.extend(numpy.transpose(k)[1])
         
     
    print x.__len__(), y.__len__()
    
    sigma_lim = bd.Otsu(sigma)
    
    pylab.figure(1)
    pylab.clf()
    pylab.imshow(numpy.log(img),interpolation='nearest')
    pylab.plot(x,y,'or')
    pylab.show()
    
    
    pylab.figure(3)
    pylab.clf()
    pylab.imshow(numpy.log(img),interpolation='nearest')
    x2=numpy.concatenate(kx)
    y2=numpy.concatenate(ky)
    print x2.__len__(), y2.__len__()
    pylab.plot(x2,y2,'or')
    pylab.show()
    
    
    pylab.figure(4)
    pylab.clf()
    pylab.imshow(numpy.log(img),interpolation='nearest')
    mask = numpy.where(sigma>sigma_lim)
    print x2[mask].__len__(), y2[mask].__len__()
    pylab.plot(x2[mask],y2[mask],'or')
    pylab.show()    