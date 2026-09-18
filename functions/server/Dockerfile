FROM fnproject/python:3.9-dev as build-stage
WORKDIR /function
ADD requirements.txt /function/
RUN pip3 install --target /python/ --no-cache-dir -r requirements.txt
ADD . /function/
RUN rm -fr /function/.pip_cache

FROM fnproject/python:3.9
WORKDIR /function
COPY --from=build-stage /python /python
COPY --from=build-stage /function /function
RUN chmod -R o+r /function
ENV PYTHONPATH=/function:/python

# fdk pulls pytest, Cython and wheel as install dependencies; a running
# function needs none of them, and every byte here is pulled before the
# first request can be served. The import check below is what makes this
# safe to delete: if any of them turn out to be needed, the build fails
# rather than production.
RUN rm -rf /python/pytest /python/_pytest /python/Cython /python/pyximport \
           /python/wheel /python/setuptools /python/pkg_resources \
           /python/pip /python/cython.py /python/py.py \
           /python/bin/pytest /python/bin/py.test /python/bin/cython \
           /python/bin/cythonize /python/bin/cygdb /python/bin/wheel

# Imports at BUILD time, where a failure lands in the CI log. A container
# that dies on import gives the API Gateway nothing to report but a bare
# 500, with no function logs to explain it.
RUN test -x /python/bin/fdk \
 && python3 -c "import sys; print('PYTHON', sys.version); \
import oci; print('OCI', oci.__version__); \
from fdk import response; print('FDK import OK'); \
import func; print('FUNC import OK, handler =', func.handler)"

ENTRYPOINT ["/python/bin/fdk", "/function/func.py", "handler"]
