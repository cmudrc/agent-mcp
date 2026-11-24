# The aircraft-analysis tools in one container, on Red Hat's minimal base.
#
# Build from a fresh clone of this repository, with the repository root as
# the build context (see container/README.md):
#
#   git clone https://github.com/cmudrc/agent-mcp && cd agent-mcp
#   docker build --platform linux/amd64 -f container/Dockerfile \
#       --build-arg AGENT_MCP_COMMIT=$(git rev-parse HEAD) -t aircraft-tools .
#
# Plain Dockerfile syntax only (no BuildKit features), so docker, podman,
# buildah and an OpenShift Docker build all read it. linux/amd64 only: the
# SU2 release binary and DLR's TiGL packages are built for that platform.
#
# The build downloads from: the UBI package repositories, micro.mamba.pm,
# conda.anaconda.org (conda-forge and DLR's dlr-sc channel), pypi.org and
# github.com. Every version is pinned (conda-linux-64.lock, constraints.txt,
# the commit ids below, SU2 8.4.0 in su2-mcp's install script).

# The full name, registry included: podman does not guess the registry of a
# short name when it has no terminal to ask on. Red Hat publishes the same
# image as registry.access.redhat.com/ubi9/ubi-minimal.
ARG BASE_IMAGE=docker.io/redhat/ubi9-minimal:latest
FROM ${BASE_IMAGE}

# The other tool repositories, at the commits this image was tested with.
ARG TIGL_MCP_COMMIT=ab3a3f88cf0c321e927798f1d9a538cdf5f8d610
ARG SU2_MCP_COMMIT=2e0bbb9752961a0cb3ca60f341975ecc6ed26dde
ARG PYCYCLE_MCP_COMMIT=020901d55a104e5415d3ba3c724a0fcd6cf0c85b
ARG NSEG_MCP_COMMIT=b1b4853a2a043cab277fef732e576788d3a36fe5
ARG AVIARY_CPACS_MCP_COMMIT=a7237bd178a92eac7abbf164a6f9a0457f753c5f
# This repository's own commit, recorded in the image (the code itself comes
# from the build context).
ARG AGENT_MCP_COMMIT=unknown
ARG MICROMAMBA_VERSION=2.0.5
ARG MICROMAMBA_SHA256=ff1a10b46e9ee673bb672ac464d4787b4e9c3569b75816a56f76dddc0b1f07fc
ARG GITHUB_ORG_URL=https://github.com/cmudrc

# 1. System libraries. The gmsh mesher loads the X and GL libraries even
#    though nothing is drawn; the flow picture is rendered off screen through
#    EGL with Mesa's software renderer (mesa-dri-drivers), so no display, no
#    Xvfb and no OSMesa are needed (UBI has neither of the last two).
RUN microdnf install -y --nodocs --setopt=install_weak_deps=0 \
        tar gzip bzip2 unzip findutils which ca-certificates procps-ng \
        mesa-libGL mesa-libEGL mesa-libGLU mesa-dri-drivers libglvnd-glx libglvnd-egl \
        libXrender libXcursor libXfixes libXft libXinerama libXext libXi \
        libSM libICE libxcb libX11 fontconfig dejavu-sans-fonts libgomp \
    && microdnf clean all

# 2. Python 3.12 with DLR's geometry kernel (TiGL, TiXI, OpenCASCADE
#    bindings). DLR publishes these as conda packages only. The lock file
#    lists the exact package files, so nothing is solved at build time.
#    The install is tried up to three times: it failed once for us on a
#    download or unpacking error (2026-11-23), and every package is checked
#    against the checksum in the lock file, so a second try cannot install
#    anything else.
COPY container/conda-linux-64.lock /opt/aircraft/container/conda-linux-64.lock
RUN cd /tmp \
    && curl -fsSL -o micromamba.tar.bz2 "https://micro.mamba.pm/api/micromamba/linux-64/${MICROMAMBA_VERSION}" \
    && echo "${MICROMAMBA_SHA256}  micromamba.tar.bz2" | sha256sum -c - \
    && tar -xjf micromamba.tar.bz2 bin/micromamba \
    && ok=0 \
    && for attempt in 1 2 3; do \
           if MAMBA_ROOT_PREFIX=/tmp/mamba bin/micromamba create -y -p /opt/aircraft/env \
                  -f /opt/aircraft/container/conda-linux-64.lock; then ok=1; break; fi; \
           echo "micromamba attempt ${attempt} failed; starting again from nothing"; \
           rm -rf /tmp/mamba /opt/aircraft/env; \
       done \
    && [ "${ok}" = 1 ] \
    && rm -rf /tmp/mamba /tmp/bin /tmp/micromamba.tar.bz2 \
    && /opt/aircraft/env/bin/python -c "import tigl3.tigl3wrapper, tixi3.tixi3wrapper; from OCC.Core.STEPControl import STEPControl_Writer"

ENV PATH=/opt/aircraft/env/bin:/opt/su2/bin:${PATH}

# 3. The solver libraries, at the versions every published number of this
#    project was made with (numpy 1.x, gmsh 4.15.2, OpenMDAO 3.36.0, pyCycle,
#    Aviary 0.9.10, pyvista 0.48.4).
COPY container/constraints.txt /opt/aircraft/container/constraints.txt
RUN python -m pip install --no-cache-dir --root-user-action=ignore \
        -c /opt/aircraft/container/constraints.txt \
        numpy pyvista matplotlib gmsh openmdao om-pycycle aviary dymos pillow lxml pyyaml \
    && rm -rf /root/.cache

# 4. The four solver servers and the Aviary server, from GitHub at the pinned
#    commits, and this repository (the gateway) from the build context.
RUN cd /opt/aircraft \
    && fetch() { mkdir "$1" && curl -fsSL "${GITHUB_ORG_URL}/$1/archive/$2.tar.gz" | tar -xz --strip-components=1 -C "$1"; } \
    && fetch tigl-mcp "${TIGL_MCP_COMMIT}" \
    && fetch su2-mcp "${SU2_MCP_COMMIT}" \
    && fetch pycycle-mcp "${PYCYCLE_MCP_COMMIT}" \
    && fetch nseg-mcp "${NSEG_MCP_COMMIT}" \
    && fetch aviary-cpacs-mcp "${AVIARY_CPACS_MCP_COMMIT}"
COPY . /opt/aircraft/agent-mcp
RUN cd /opt/aircraft \
    && python -m pip install --no-cache-dir --root-user-action=ignore \
        -c /opt/aircraft/container/constraints.txt \
        -e ./tigl-mcp -e ./su2-mcp -e ./pycycle-mcp -e ./nseg-mcp -e ./aviary-cpacs-mcp -e ./agent-mcp \
    && python -m pip check \
    && { python -m compileall -q /opt/aircraft/*-mcp >/dev/null || true; } \
    && rm -rf /root/.cache

# 5. SU2 8.4.0, with the project's own install script (official release
#    binary, statically linked; the script refuses to finish unless SU2_CFD
#    runs and reports that version).
RUN SU2_INSTALL_DIR=/opt/su2 bash /opt/aircraft/su2-mcp/scripts/install_su2.sh \
    && SU2_CFD -h | head -1 \
    && rm -rf /root/.cache

# Start-up script, the self-test command, the list of what is installed, and
# an empty, group-writable /data.
RUN chmod 0755 /opt/aircraft/agent-mcp/container/entrypoint.sh \
    && printf '#!/bin/sh\nexec /opt/aircraft/agent-mcp/container/entrypoint.sh selftest "$@"\n' > /usr/local/bin/aircraft-selftest \
    && chmod 0755 /usr/local/bin/aircraft-selftest \
    && { echo "agent-mcp ${AGENT_MCP_COMMIT}"; echo "tigl-mcp ${TIGL_MCP_COMMIT}"; echo "su2-mcp ${SU2_MCP_COMMIT}"; \
         echo "pycycle-mcp ${PYCYCLE_MCP_COMMIT}"; echo "nseg-mcp ${NSEG_MCP_COMMIT}"; \
         echo "aviary-cpacs-mcp ${AVIARY_CPACS_MCP_COMMIT}"; echo "base $(cat /etc/redhat-release)"; \
         echo "su2 $(SU2_CFD -h | head -1 | sed 's/^ *//')"; echo "su2_cfd_sha256 $(sha256sum /opt/su2/bin/SU2_CFD | cut -d' ' -f1)"; \
         python -c "import sys, numpy, vtk, gmsh, openmdao, aviary, fastmcp; print('python', sys.version.split()[0]); print('numpy', numpy.__version__); print('gmsh', gmsh.__version__); print('vtk', vtk.vtkVersion.GetVTKVersion()); print('openmdao', openmdao.__version__); print('aviary', aviary.__version__); print('fastmcp', fastmcp.__version__)" 2>/dev/null; \
       } > /opt/aircraft/VERSIONS.txt \
    && cat /opt/aircraft/VERSIONS.txt \
    && rm -rf /data /root/.cache && mkdir /data && chgrp 0 /data && chmod 0775 /data

# 6. Runtime layout. /data is the only place anything is written: session
#    logs, solver output, working copies of the aircraft files. OpenShift
#    runs the container as an arbitrary user id in group 0, so /data belongs
#    to group 0 and is group-writable; mount a volume there to keep records.
#    These are set after the last RUN on purpose, so that no build step
#    leaves a root-owned cache folder in /data.
#    FORWARDED_ALLOW_IPS: the web server believes the X-Forwarded-Proto header
#    of whatever proxy is in front (an OpenShift route ends TLS and passes
#    plain HTTP on), so its one redirect, /mcp/ to /mcp, keeps https.
ENV HOME=/data \
    AIRCRAFT_DATA_DIR=/data \
    AIRCRAFT_RUNS_DIR=/data/aircraft-runs \
    AIRCRAFT_MCP_STATE_DIR=/data/.aircraft-mcp \
    AIRCRAFT_OUTPUT_ROOT=/data/pipeline_output \
    AIRCRAFT_SELFTEST_DIR=/data/selftest \
    AIRCRAFT_STUDY_FILES=/opt/aircraft/agent-mcp/kiro/study/files \
    AIRCRAFT_SU2_BIN=/opt/su2/bin \
    AIRCRAFT_DASHBOARD_HOST=0.0.0.0 \
    AIRCRAFT_DASHBOARD_ALLOWED_HOSTS=* \
    FORWARDED_ALLOW_IPS=* \
    OPENMDAO_REPORTS=0 \
    PYVISTA_OFF_SCREEN=true \
    VTK_DEFAULT_OPENGL_WINDOW=vtkEGLRenderWindow \
    PYTHONUNBUFFERED=1
WORKDIR /data
USER 1001:0
# 8800: the tools (MCP over HTTP, path /mcp). 8765: the status page.
EXPOSE 8800 8765
ENTRYPOINT ["/opt/aircraft/agent-mcp/container/entrypoint.sh"]
CMD ["serve"]
