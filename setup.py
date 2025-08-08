from setuptools import setup
import logging
from setuptools.extension import Extension
from Cython.Build import cythonize
import shutil
import os
from setuptools.command.build_ext import build_ext

# Logging konfigurieren
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

################################################################################
# Cython Setup
################################################################################

"""Build Cython extensions for both optimized and compatible variants.

Updated to work with src/ layout where sources live under src/loxwebsocket.
"""

# Always build both variants; runtime will pick the right module
logger.info("Building Cython extensions: optimized and compatible (both)")
build_variants = {
    "optimized": ["-O3", "-march=native", "-ffast-math"],
    "compatible": ["-O2", "-mtune=generic"],
}

source_dir = os.path.join("src", "loxwebsocket", "cython_modules")

cython_extensions = []
for variant, compile_args in build_variants.items():
    ext_name = f"loxwebsocket.cython_modules.extractor_{variant}"
    pyx_original = os.path.join(source_dir, "extractor.pyx")
    pyx_variant = os.path.join(source_dir, f"extractor_{variant}.pyx")

    # Copy the original .pyx to a variant-specific file
    shutil.copyfile(pyx_original, pyx_variant)

    ext = Extension(
        ext_name,
        sources=[pyx_variant],
        extra_compile_args=compile_args,
        extra_link_args=compile_args,
        define_macros=[("CYTHON_BUILD_VARIANT", f'"{variant}"')]
    )
    cy_ext = cythonize(
        ext,
        force=True,
        cache=False,
        language_level="3",
        compiler_directives={
            'boundscheck': False,
            'wraparound': False,
            'cdivision': True,
            'nonecheck': False,
            'initializedcheck': False,
            'embedsignature': False,
        }
    )
    cython_extensions.extend(cy_ext)

################################################################################
# Setup call for Cython extensions
################################################################################

class CleanUpBuildExt(build_ext):
    def run(self):
        # Run the standard build_ext command
        super().run()
        # Remove the variant-specific .pyx files after compilation
        for variant in ("optimized", "compatible"):
            variant_file = os.path.join("src", "loxwebsocket", "cython_modules", f"extractor_{variant}.pyx")
            if os.path.exists(variant_file):
                os.remove(variant_file)
                print(f"Removed variant-specific file: {variant_file}")

cmdclass = {"build_ext": CleanUpBuildExt}

setup(
    name="loxwebsocket",
    ext_modules=cython_extensions,
    cmdclass=cmdclass,
    zip_safe=False,
)