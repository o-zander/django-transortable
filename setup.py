from setuptools import setup, find_packages
import os
import transortable

CLASSIFIERS = [
    'Development Status :: 3 - Alpha',
    'Environment :: Web Environment',
    'Framework :: Django',
    'Intended Audience :: Developers',
    'Operating System :: OS Independent',
    'Programming Language :: Python',
    'Programming Language :: Python :: 2.7',
    'Topic :: Database'
]

setup(
    author="Oliver Zander",
    author_email="oliver.zander@gmail.com",
    name='django-transortable',
    version=transortable.__version__,
    description='Translatable & Sortable Models for Django',
    long_description=open(os.path.join(os.path.dirname(__file__), 'README.md')).read(),
    url='https://github.com/o-zander/django-transortable',
    platforms=['OS Independent'],
    classifiers=CLASSIFIERS,
    install_requires=[
        'Django>=1.6,<1.9',
        'django-admin-sortable>=1.7.7',
        'django-hvad>=0.5.1'
    ],
    packages=find_packages(),
    include_package_data=True,
    package_data = {'': ['*.html', '*.css', '*.js', '*.png']},
    zip_safe=False,
)