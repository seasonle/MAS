import numpy as np
from matplotlib import pyplot as plt
from functools import partial
from scipy.ndimage import map_coordinates, fourier_shift
from scipy.ndimage.interpolation import shift
from scipy.optimize import curve_fit, minimize
from scipy.signal import convolve2d
from abel.tools.polar import polar2cart, cart2polar, index_coords
from itertools import combinations
from skimage.feature.register_translation import _upsampled_dft
from skimage.transform import resize
from skimage.draw import line_aa
from mas.decorators import np_gpu
from mas.deconvolution.admm import patch_based, TV, bm3d_pnp, dncnn_pnp
from mas.deconvolution import tikhonov, admm
from mas.forward_model import size_equalizer
from mas.psf_generator import PSFs, circ_incoherent_psf
from mas.misc import xy2rc, rc2xy

from tqdm import tqdm
import inspect, copy

def guizar_multiframe(corr_sum, upsample_factor=100, start=10, end=30, np=np):
    """
    Efficient subpixel image translation registration by cross-correlation.
    Modified and simplified version of register_translation from skimage

    Args:
        corr_sum (ndarray): correlated and summed input frame groups
        upsample_factor (int): Upsampling factor

    Returns:
        shifts : ndarray
    """

    shape = corr_sum[0].shape

    d = []
    for time_diff, cross_correlation in enumerate(corr_sum[start - 1:end - 1]):

        time_diff += start

        # Locate maximum
        maxima = np.unravel_index(np.argmax(np.abs(cross_correlation)),
                                cross_correlation.shape)
        midpoints = np.array([np.fix(axis_size / 2) for axis_size in shape])

        shifts = np.array(maxima, dtype=np.float64)
        shifts[shifts > midpoints] -= np.array(shape)[shifts > midpoints]

        # Initial shift estimate in upsampled grid
        shifts = np.around(shifts * upsample_factor) / upsample_factor
        # cast cupy.ndarray -> int
        upsampled_region_size = int(np.ceil(upsample_factor * 1.5))
        # Center of output array at dftshift + 1
        dftshift = np.fix(upsampled_region_size / 2.0)
        upsample_factor = np.array(upsample_factor, dtype=np.float64)
        # normalization = (src_freq.size * upsample_factor ** 2)
        # Matrix multiply DFT around the current shift estimate
        sample_region_offset = dftshift - shifts*upsample_factor
        cross_correlation = _upsampled_dft(
            # image_product.conj(),
            np.fft.ifftn(cross_correlation).conj(),
            upsampled_region_size,
            upsample_factor,
            sample_region_offset
        ).conj()
        # cross_correlation /= normalization
        # Locate maximum and map back to original pixel grid
        maxima = np.unravel_index(
            np.argmax(np.abs(cross_correlation)),
            cross_correlation.shape
        )
        CCmax = cross_correlation[maxima]

        maxima = np.array(maxima, dtype=np.float64) - dftshift

        shifts = shifts + maxima / upsample_factor

        d.append(np.array((shifts[1], -shifts[0])) / time_diff)
    d = np.array(d)
    guizar_error = np.mean(d, axis=0)

    return guizar_error, d


@np_gpu(np_args=[0])
def correlate_and_sum(frames, mode='CC', np=np):
    """Correlate all frame combinations and sum each group

    Args:
        frames (ndarray): input images
        mode (str, default='PC'): type of correlation to use. ('PC', 'NCC', 'CC')

    Returns:
        ndarray: axes (group, corr_x_coord, corr_y_coord)
    """

    frames_freq = np.fft.fftn(frames, axes=(1, 2))

    product_sums = np.zeros(
        (len(frames) - 1, frames.shape[1], frames.shape[2]),
        dtype='complex128'
    )
    for time_diff in tqdm(range(1, len(frames_freq)), desc='Correlation', leave=None):
        products = frames_freq[:-time_diff] * frames_freq[time_diff:].conj()
        if mode.upper() == 'PC':
            product_sums[time_diff - 1] = np.sum(products / np.abs(products), axis=0)
        elif mode.upper() == 'CC':
            product_sums[time_diff - 1] = np.sum(products, axis=0)
        else:
            raise Exception('Invalid mode {}'.format(mode.upper()))

    return np.fft.ifftn(np.array(product_sums), axes=(1, 2))

# def phase_correlate(x, y):
#     """Perform normalized phase-correlation"""

#     return np.fft.ifftn(
#         np.fft.fftn(x) * np.fft.fftn(y).conj() /
#         np.abs(np.fft.fftn(x) * np.fft.fftn(y))
#     )

# def correlate(x, y):
#     """Perform cross correlation"""

#     return np.fft.ifftn(
#         np.fft.fftn(x) * np.fft.fftn(y).conj()
#     )



# def phase_correlate_cupy(x, y):
#     """Perform normalized phase-correlation"""

#     import cupy

#     if not (type(x) is cupy.ndarray and type(y) is cupy.ndarray):
#         x, y = cupy.array(x), cupy.array(y)

#     return cupy.fft.ifftn(
#         cupy.fft.fftn(x) * cupy.fft.fftn(y).conj() /
#         cupy.abs(cupy.fft.fftn(x) * cupy.fft.fftn(y))
#     )

# def correlate_cupy(x, y):
#     """Perform cross-correlation"""

#     import cupy

#     if not (type(x) is cupy.ndarray and type(y) is cupy.ndarray):
#         x, y = cupy.array(x), cupy.array(y)

#     return cupy.fft.ifftn(
#         cupy.fft.fftn(x) * cupy.fft.fftn(y).conj()
#     )


# def multi_register_cupy(images, method='correlation'):
#     import cupy

#     images = cupy.array(images)

#     correlations = cupy.zeros(
#         (len(images) - 1, images.shape[1], images.shape[2]),
#         dtype='complex128'
#     )

#     for i, j in combinations(range(len(images)), 2):
#         print('Correlation {}/{}\r'.format(i + 1, len(images) - 1), end='')
#         if method == 'phase':
#             correlations[j - i - 1] += phase_correlate_cupy(images[i], images[j])
#         else:
#             correlations[j - i - 1] += correlate_cupy(images[i], images[j])

#     argmaxes = []
#     for correlation in correlations:
#         argmaxes.append(
#             np.unravel_index(
#                 np.argmax(
#                     cupy.asnumpy(correlation)
#                 ),
#                 correlations[0].shape
#             )
#         )
#     argmaxes = np.array(argmaxes)
#     correlations = cupy.asnumpy(correlations)

#     return argmaxes, correlations

def roll(x, shift):
    shift = np.round(shift).astype(int)
    return np.roll(
        np.roll(
            x,
            shift[0],
            axis=0
        ),
        shift[1],
        axis=1
    )

def shift_and_sum(frames, drift, mode='full', shift_method='roll'):
    """Coadd frames by given shift

    Args:
        frames (ndarray): input frames to coadd
        drift (ndarray): drift between adjacent frames (cartesian)
        mode (str): zeropad before coadding ('full') or crop to region of
            frame overlap ('crop')
        shift_method (str): method for shifting frames ('roll', 'fourier')
        pad (bool): zeropad images before coadding

    Returns:
        (ndarray): coadded images
    """

    pad = np.ceil(drift * (len(frames) - 1)).astype(int)
    pad_x = (0, pad[0]) if drift[0] > 0 else (-pad[0], 0)
    pad_y = (pad[1], 0) if drift[1] > 0 else (0, -pad[1])
    frames = np.pad(frames, ((0, 0), pad_y, pad_x), mode='constant')

    summation = np.zeros(frames[0].shape, dtype='complex128')

    for time_diff, frame in enumerate(frames):
        shift = np.array(drift) * (time_diff + 1)
        if shift_method == 'roll':
            integer_shift = np.round(shift).astype(int)
            shifted = roll(frame, (-integer_shift[1], integer_shift[0]))
        elif shift_method == 'fourier':
            shifted = np.fft.ifftn(fourier_shift(
                np.fft.fftn(frame),
                (-shift[1], shift[0])
            ))
        else:
            raise Exception('Invalid shift_method')
        summation += shifted

    if mode == 'crop':
        summation = size_equalizer(
            summation,
            np.array(frames[0].shape).astype(int) -
            2 * np.ceil(xy2rc(drift) * (len(frames)-1)).astype(int)
        )
    elif mode == 'full':
        pass
    else:
        raise Exception('Invalid mode')

    return summation.real

def ulas_multiframe(frames, proportion=0.4):
    num_frames = frames.shape[0]

    # initialize the array that holds fourier transforms of the correlations between frame pairs
    correlations = np.zeros((num_frames-1, frames.shape[1], frames.shape[2]))

    for i in range(num_frames-1):
        for j in np.arange(i+1, num_frames):
            # k^th element of correlations_f is the sum of all correlations between
            # frame pairs that are k frame apart from each other.
            # correlations[j - i - 1] += np.fft.fftn(frames[i]) * np.fft.fftn(frames[j]).conj()
            correlations[j - i - 1] += np.fft.ifft2(np.fft.fft2(frames[i]) * np.fft.fft2(frames[j]).conj()).real

    # %% foo

    shifts = np.zeros((correlations.shape[0], 2))

    for i in range(correlations.shape[0]):
        # find the argmax points, which give the estimated shift
        shifts[i] = np.unravel_index(np.argmax(correlations[i]), correlations[i].shape)

    # bring the shift values from [0,N] to [-N/2, N/2] format
    shifts[shifts>np.fix(frames[0].shape[0]/2)] -= frames[0].shape[0]

    # normalize the shifts to per frame shift
    shifts = shifts / np.tile(np.arange(1,shifts.shape[0]+1), (2,1)).T

    # determine what proportion of the shift estimates to use in the final shift estimation
    # if `proportion` < 1, then we are not using the correlations of frame pairs that
    # are very far from each other, the reason being that further apart frames have
    # less overlap, where the nonoverlapping parts contribute to the correlation as
    # 'noise', and reduce the accuracy.
    # proportion = 0.4

    # estimate the shift using the first `proportion` of the shift array
    shift_est = np.mean(shifts[:int(proportion * shifts.shape[0])], axis=0)


    # initialize the array that will take the fourier transform of the correlations of correlations
    correlations_f2 = np.zeros((num_frames-2, frames.shape[1], frames.shape[2])).astype(np.complex128)

    for i in range(num_frames-2):
        for j in np.arange(i+1, num_frames-1):
            # compute the correlations between correlations to get a more refined estimate of drift
            correlations_f2[j-i-1] += np.fft.fftn(correlations[i]) * np.fft.fftn(correlations[j]).conj()

    correlations2 = np.fft.ifft2(correlations_f2).real

    # FIXME
    # for i in range(len(correlations2)):
    #     # convolve the correlations with a gaussian to eliminate outlier peaks
    #     correlations2[i] = gaussian_filter(correlations2[i], sigma=1, mode='wrap')

    shifts2 = np.zeros((correlations2.shape[0], 2))

    for i in range(correlations2.shape[0]):
        shifts2[i] = np.unravel_index(np.argmax(correlations2[i]), correlations2[i].shape)

    shifts2[shifts2>np.fix(frames[0].shape[0]/2)] -= frames[0].shape[0]
    shifts2 = shifts2 / np.tile(np.arange(1,shifts2.shape[0]+1), (2,1)).T

    shift_est2 = np.mean(shifts2[:int(proportion * shifts2.shape[0])], axis=0)
    return np.array((shift_est[1], shift_est[0])), np.array((shift_est2[1], shift_est2[0]))

@np_gpu(np_args=[0])
def ulas_multiframe(corr_sum, proportion=0.4, np=np):
    num_frames = len(corr_sum) + 1

    correlations = corr_sum

    shifts = np.zeros((correlations.shape[0], 2))

    for i in range(correlations.shape[0]):
        # find the argmax points, which give the estimated shift
        shifts[i] = np.array( # cupy requires this to be an array, not tuple
            np.unravel_index(np.argmax(correlations[i]), correlations[i].shape)
        )

    # bring the shift values from [0,N] to [-N/2, N/2] format
    shifts[shifts>np.fix(corr_sum[0].shape[0]/2)] -= corr_sum[0].shape[0]

    # normalize the shifts to per frame shift
    shifts = shifts / np.tile(np.arange(1,shifts.shape[0]+1), (2,1)).T

    # determine what proportion of the shift estimates to use in the final shift estimation
    # if `proportion` < 1, then we are not using the correlations of frame pairs that
    # are very far from each other, the reason being that further apart frames have
    # less overlap, where the nonoverlapping parts contribute to the correlation as
    # 'noise', and reduce the accuracy.
    # proportion = 0.4

    # estimate the shift using the first `proportion` of the shift array
    shift_est = np.mean(shifts[:int(proportion * shifts.shape[0])], axis=0)

    # initialize the array that will take the fourier transform of the correlations of correlations
    correlations_f2 = np.zeros((num_frames-2, corr_sum.shape[1], corr_sum.shape[2])).astype(np.complex128)

    for i in range(num_frames-2):
        for j in np.arange(i+1, num_frames-1):
            # compute the correlations between correlations to get a more refined estimate of drift
            correlations_f2[j-i-1] += np.fft.fftn(correlations[i]) * np.fft.fftn(correlations[j]).conj()

    correlations2 = np.fft.ifft2(correlations_f2).real

    # FIXME
    # for i in range(len(correlations2)):
    #     # convolve the correlations with a gaussian to eliminate outlier peaks
    #     correlations2[i] = gaussian_filter(correlations2[i], sigma=1, mode='wrap')

    shifts2 = np.zeros((correlations2.shape[0], 2))

    for i in range(correlations2.shape[0]):
        shifts2[i] = np.array( # cupy requires this to be an array, not tuple
            np.unravel_index(np.argmax(correlations2[i]), correlations2[i].shape)
        )

    shifts2[shifts2>np.fix(corr_sum[0].shape[0]/2)] -= corr_sum[0].shape[0]
    shifts2 = shifts2 / np.tile(np.arange(1,shifts2.shape[0]+1), (2,1)).T

    shift_est2 = -np.mean(shifts2[:int(proportion * shifts2.shape[0])], axis=0)
    return np.array((shift_est[1], shift_est[0])), np.array((shift_est2[1], shift_est2[0]))


def motion_deblur(*, sv, registered, drift):
    """

    """
    pixel_size_um = sv.pixel_size * 1e6

    # width of the final motion blur kernel with CCD pixel size
    kernel_size = 11
    (x,y) = (pixel_size_um * drift[0], pixel_size_um * drift[1])
    N = int(np.ceil(np.max((abs(x),abs(y)))))

    # set the shape of the initial kernel with 1 um pixels based on the estimated drift
    kernel_um = np.zeros((2*N+1, 2*N+1))

    # calculate the line representing the motion blur
    rr, cc, val = line_aa(
        N + np.round((y/2)).astype(int),
        N - np.round((x/2)).astype(int),
        N - np.round((y/2)).astype(int),
        N + np.round((x/2)).astype(int),
    )

    # update the kernel with the calculated line
    kernel_um[rr,cc] = val

    # resize the initial 1 um kernel to the given pixel size
    kernel = resize(size_equalizer(kernel_um, [int(pixel_size_um)*kernel_size]*2), [kernel_size]*2, anti_aliasing=True)
    # compute the analytical photon sieve PSF with the given pixel size
    psfs = copy.deepcopy(sv.psfs)

    # convolve the photon sieve PSF with the motion blur kernel to find the "effective blurring kernel"
    psfs.psfs[0,0] = convolve2d(psfs.psfs[0,0], kernel, mode='same')

    # normalize the kernel
    psfs.psfs[0,0] /= psfs.psfs[0,0].sum()

    # normalize the registered image (doesn't change anything but helps choosing regularization parameter consistently)
    registered /= registered.max()

    # do a tikhonov regularized deblurring on the registered image to remove the
    # in-frame blur with the calculated "effective blurring kernel"
    recon_tik = tikhonov(
        measurements=registered[np.newaxis,:,:],
        psfs=psfs,
        tikhonov_lam=1e1,
        tikhonov_order=1
    )
    plt.figure()
    plt.imshow(recon_tik[0], cmap='gist_heat')
    plt.title('Deblurred Tikhonov')
    plt.show()

    # do a Plug and Play with BM3D reconstruction with tikhonov initialization
    recon = admm(
        measurements=registered[np.newaxis,:,:],
        psfs=psfs,
        regularizer=partial(bm3d_pnp),
        recon_init=recon_tik,
        plot=False,
        iternum=5,
        periter=1,
        nu=10**-0.0,
        lam=[10**-0.5]
    )

    plt.figure()
    plt.imshow(recon[0], cmap='gist_heat')
    plt.title('Deblurred')
    plt.show()

    return recon


def ulas_multiframe3(corr_sum, proportion=0.4):
    import cupy

    if type(corr_sum) is not cupy.ndarray:
        corr_sum = cupy.array(corr_sum)

    num_frames = len(corr_sum) + 1

    correlations = corr_sum

    # %% foo

    shifts = cupy.zeros((correlations.shape[0], 2))

    for i in range(correlations.shape[0]):
        # find the argmax points, which give the estimated shift
        shifts[i] = cupy.array(cupy.unravel_index(cupy.argmax(correlations[i]), correlations[i].shape))

    # bring the shift values from [0,N] to [-N/2, N/2] format
    shifts[shifts>cupy.fix(corr_sum[0].shape[0]/2)] -= corr_sum[0].shape[0]

    # determine what proportion of the shift estimates to use in the final shift estimation
    # if `proportion` < 1, then we are not using the correlations of frame pairs that
    # are very far from each other, the reason being that further apart frames have
    # less overlap, where the nonoverlapping parts contribute to the correlation as
    # 'noise', and reduce the accuracy.
    # proportion = 0.4

    # normalize the shifts to per frame shift
    shifts = shifts / cupy.tile(cupy.arange(1,shifts.shape[0]+1), (2,1)).T

    # estimate the shift using the first `proportion` of the shift array
    shift_est = cupy.asnumpy(cupy.mean(shifts[:int(proportion * shifts.shape[0])], axis=0))

    # initialize the array that will take the fourier transform of the correlations of correlations
    correlations_f2 = cupy.zeros((num_frames-2, corr_sum.shape[1], corr_sum.shape[2])).astype(cupy.complex128)

    for i in range(num_frames-2):
        for j in cupy.arange(i+1, num_frames-1):
            # compute the correlations between correlations to get a more refined estimate of drift
            correlations_f2[j-i-1] += cupy.fft.fftn(correlations[i]) * cupy.fft.fftn(correlations[j]).conj()
    correlations2 = cupy.fft.ifft2(correlations_f2).real

    # FIXME
    # for i in range(len(correlations2)):
    #     # convolve the correlations with a gaussian to eliminate outlier peaks
    #     correlations2[i] = gaussian_filter(correlations2[i], sigma=1, mode='wrap')

    shifts2 = cupy.zeros((correlations2.shape[0], 2))

    for i in range(correlations2.shape[0]):
        shifts2[i] = cupy.array(cupy.unravel_index(cupy.argmax(correlations2[i]), correlations2[i].shape))

    shifts2[shifts2>cupy.fix(corr_sum[0].shape[0]/2)] -= corr_sum[0].shape[0]
    shifts2 = shifts2 / cupy.tile(cupy.arange(1,shifts2.shape[0]+1), (2,1)).T
    shift_est2 = -cupy.asnumpy(cupy.mean(shifts2[:int(proportion * shifts2.shape[0])], axis=0))
    return ((shift_est[1], shift_est[0])), ((shift_est2[1], shift_est2[0]))
