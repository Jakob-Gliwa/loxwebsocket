from setuptools import setup
import logging
from setuptools.extension import Extension
from Cython.Build import cythonize
import shutil
import os
from setuptools.command.build_ext import build_ext
import platform
from distutils.errors import CompileError

# Logging konfigurieren
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

################################################################################
# Cython Setup
################################################################################

# Plattform bestimmen
arch = platform.uname().machine.lower()
logger.info(f"Detected platform: {arch}")

# Define build variants based on architecture
if arch in ("x86_64", "amd64"):
    logger.info("Building Cython extensions for AMD64 architecture - optimized & compatible versions")
    build_variants = {
        "optimized": ["-O3", "-march=native", "-ffast-math"],
        "compatible": ["-O2", "-mtune=generic"]
    }
else:
    logger.info("Building Cython extensions for non-AMD64 architecture - compatible version only")
    build_variants = {
        "compatible": ["-O2", "-mtune=generic"]
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
# Custom build_ext: erst normal, bei Fehler Retry ohne -march
################################################################################
class CleanUpBuildExt(build_ext):
    def build_extensions(self):
        try:
            super().build_extensions()
        except CompileError as e:
            logger.warning("CompileError erkannt – retry ohne '-march=native'")
            # entferne '-march=native' aus allen Extensions
            for ext in self.extensions:
                ext.extra_compile_args = [
                    flag for flag in ext.extra_compile_args
                    if not flag.startswith("-march")
                ]
                ext.extra_link_args = [
                    flag for flag in ext.extra_link_args
                    if not flag.startswith("-march")
                ]
            # nochmal versuchen
            super().build_extensions()

    def run(self):
        # Standard build_ext (führt build_extensions oben aus)
        super().run()
        # Cleanup: temporäre .pyx-Dateien löschen
        for variant in build_variants:
            variant_file = os.path.join(
                source_dir,
                f"extractor_{variant}.pyx"
            )
            if os.path.exists(variant_file):
                os.remove(variant_file)
                logger.info(f"Removed variant-specific file: {variant_file}")

cmdclass = {"build_ext": CleanUpBuildExt}

################################################################################
# setup()-Aufruf
################################################################################
setup(
    name="loxwebsocket",
    ext_modules=cython_extensions,
    cmdclass=cmdclass,
    zip_safe=False,
)