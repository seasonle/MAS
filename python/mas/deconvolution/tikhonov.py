import numpy as np
from mas.block import block_mul, block_inv
from mas.deconvolution.common import get_LAM

def tikhonov(
            *,
            sources,
            psfs,
            measurements,
            tikhonov_lam,
            tikhonov_order
):
    """Perform Tikhonov regularization based image reconstruction for PSSI.

    Solves x_hat = argmin_{x} { ||Ax-y||_2^2 + lam * ||Dx||_2^2 }. D is the
    discrete derivative operator of order `tikhonov_order`.

    Args:
        sources (ndarray): 4d array of ground truth spectral images
        psfs (PSFs): PSFs object containing psfs and other csbs state data
        measured_noisy (ndarray): 4d array of noisy measurements
        tikhonov_lam (float): regularization parameter of tikhonov
        tikhonov_order (int): [0,1 or 2] order of the discrete derivative
            operator used in the tikhonov regularization

    Returns:
        4d array of the reconstructed images
    """
    k, num_sources = psfs.selected_psfs.shape[:2]
    aa, bb = sources.shape[1:]
    # DFT of the kernel corresponding to (D^TD)
    LAM = get_LAM(rows=aa,cols=bb,order=tikhonov_order)
    return np.real(
        np.fft.ifft2(
                block_mul(
                    block_inv(
                        psfs.selected_GAM +
                        tikhonov_lam * np.einsum('ij,kl', np.eye(num_sources), LAM)
                    ),
                    block_mul(
                        psfs.selected_psf_dfts_h,
                        np.fft.fft2(measurements)
                    )
                )
        )
    )
