# Milliarc-second Photon Sieve Simulations

UIUC-SINE Group

#### Files

- `csbs.py` - clustered SBS algorithm with interchangeable cost modules
  [paper](https://ieeexplore.ieee.org/document/4429318/)
- `psf_generator.py` - functions for generating PSFs at different measurement planes
- `random_cost.py` - a simple example cost module for CSBS
- `plotting.py` - functions for displaying results from CSBS
- `forward_model.py` - functions for simulating observations at measurement planes generated by CSBS algorithm
- `examples/` - directory containing python snippets

#### Dependencies
- Fedora
  - libtiff-devel
  - fftw-devel
  
#### Installation

    cd mas/python
    pip install -e .

#### Running tests

    python tests/tests.py
