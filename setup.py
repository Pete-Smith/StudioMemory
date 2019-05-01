import datetime

from setuptools import setup, find_packages
import pkg_resources

setup(
    name=pkg_resources.safe_name('studio-memory'),
    version=datetime.date.today().isoformat().replace('-','.'),
    packages=find_packages(),
    include_package_data = True,
    install_requires=['pyqt5', 'sqlalchemy',],
    author="Pete Smith",
    author_email="psmith at anagogical dot net",
    description= 'Simple Structured Outliner and Kanban Board',
    license="Copyright {0} P.F. Smith".format(datetime.date.today().year),
    zip_safe=False,
    entry_points={
        'console_scripts' : [
            '',
        ],
        'gui_scripts': [
            'StudioMemory=studio_memory.gui:main',
        ],
    },
)
