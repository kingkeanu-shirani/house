"""Optional pip packaging; running from the extracted folder needs no install."""
from pathlib import Path
import shutil
from setuptools import setup, find_packages
from setuptools.command.build_py import build_py


class Build(build_py):
    def run(self):
        super().run()
        root=Path(__file__).resolve().parent
        out=Path(self.build_lib)/'house_sim/resources'
        for folder in ('catalog','tasks','reports','demos'):
            shutil.copytree(root/folder,out/folder,dirs_exist_ok=True,ignore=shutil.ignore_patterns('split_key','test_manifest.json','test_phrasings.json','*.key'))
        shutil.copy2(root/'coverage.csv',out/'coverage.csv')


setup(name='house-ai-symbolic',version='2.0.0',python_requires='>=3.9',
      description='Dependency-free symbolic household decision environment',
      packages=find_packages(),package_data={'house_sim':['data/*.json','data/types/*.json','data/physics/*.json','data/robot/*.json',
                                                  'data/calendar/*.json','data/vocab/*.json','data/generator/*.json',
                                                  'eval/*.json','eval/*.html']},
      entry_points={'console_scripts':['house-ai=house_sim.cli:main']},
      cmdclass={'build_py':Build},install_requires=[])
