DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null && pwd )"
DEPS="${DIR}/deps"
BUILD_PREFIX="${DEPS}/ffmpeg_build"
BIN_DIR="${HOME}/bin"

FFMPEG_TAG="n4.1.3"
VMAF_TAG="v2.0.0"
AOM_TAG="v1.0.0"

# install Netflix/vmaf dependecies
sudo apt-get update -qq && \
sudo apt-get install -y \
    pkg-config gfortran libhdf5-dev libfreetype6-dev liblapack-dev \
    python3 \
    python3-dev \
    python3-pip \
    python3-setuptools \
    python3-tk

pip3 install -r my_requirements.txt

# Installs ffmpeg from source (HEAD) with libaom and libx265, as well as a few
# other common libraries
̈́
sudo apt -y install \
  autoconf \
  automake \
  build-essential \
  cmake \
  git \
  libass-dev \
  libfreetype6-dev \
  libsdl2-dev \
  libtheora-dev \
  libtool \
  libva-dev \
  libvdpau-dev \
  libvorbis-dev \
  libxcb1-dev \
  libxcb-shm0-dev \
  libxcb-xfixes0-dev \
  mercurial \
  pkg-config \
  texinfo \
  wget \
  zlib1g-dev \
  yasm \
  libvpx-dev \
  libopus-dev \
  libx264-dev \
  libmp3lame-dev \
  libfdk-aac-dev

# Install libaom from source.
if [ ! -d "${DEPS}/fdk-aac" ]; then
  echo "[install] Building fdk-aac from source..."
  mkdir -p deps
  pushd deps > /dev/null

  git clone --depth 1 https://github.com/mstorsjo/fdk-aac.git 
  pushd fdk-aac > /dev/null 
  autoreconf -fiv 
  ./configure --prefix="${BUILD_PREFIX}" --disable-shared
  make -j8
  make install
  popd > /dev/null
  popd > /dev/null
else
  echo "[install] fdk-aac already present, skipping."
fi

# Install libx265 from source.
if [ ! -d "${DEPS}/x265_git" ]; then
  echo "[install] Building x265 from source..."
  pushd deps > /dev/null
  git clone https://bitbucket.org/multicoreware/x265_git.git
  pushd x265_git/build/linux > /dev/null
  cmake -G "Unix Makefiles" -DCMAKE_INSTALL_PREFIX="$DEPS/ffmpeg_build" -DENABLE_SHARED:bool=off ../../source
  make
  make install
  popd > /dev/null
  popd > /dev/null
else
  echo "[install] x265 already present, skipping."
fi

# ---- libaom (required by --enable-libaom; original script never built it) ----
if [ ! -d "${DEPS}/aom" ]; then
  echo "[install] Building libaom (${AOM_TAG})..."
  pushd "${DEPS}" >/dev/null
  git clone https://aomedia.googlesource.com/aom
  pushd aom >/dev/null
  git checkout "${AOM_TAG}" || true
  mkdir -p build && cd build
  cmake -G "Unix Makefiles" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${BUILD_PREFIX}" \
    -DBUILD_SHARED_LIBS=0 \
    -DENABLE_TESTS=0 \
    -DENABLE_DOCS=0 \
    ..
  make -j"$(nproc)"
  make install
  popd >/dev/null
  popd >/dev/null
else
  echo "[install] libaom already present, skipping."
fi

# ---- ffmpeg (pin instead of snapshot HEAD) ----
if [ ! -d "${DEPS}/ffmpeg" ]; then
  echo "[install] Fetching ffmpeg (${FFMPEG_TAG})..."
  pushd "${DEPS}" >/dev/null
  git clone https://git.ffmpeg.org/ffmpeg.git ffmpeg

  echo "[install] Building ffmpeg (${FFMPEG_TAG})..."
  pushd "ffmpeg" >/dev/null
  git fetch --tags -f
  git checkout "${FFMPEG_TAG}"

  PKG_CONFIG_PATH="${BUILD_PREFIX}/lib/pkgconfig" ./configure \
    --prefix="${BUILD_PREFIX}" \
    --pkg-config-flags="--static" \
    --extra-cflags="-I${BUILD_PREFIX}/include" \
    --extra-ldflags="-L${BUILD_PREFIX}/lib" \
    --extra-libs="-lpthread -lm" \
    --bindir="${BIN_DIR}" \
    --enable-gpl \
    --enable-libass \
    --enable-libfdk-aac \
    --enable-libmp3lame \
    --enable-libx264 \
    --enable-libx265 \
    --enable-libtheora \
    --enable-libfreetype \
    --enable-libvorbis \
    --enable-libopus \
    --enable-libvpx \
    --enable-libaom \
    --enable-nonfree

  make
  make install
  hash -r
  popd >/dev/null
  popd >/dev/null
else
  echo "[install] ffmpeg already present, skipping."
fi

# ---- ninja (prefer apt ninja-build; source bootstrap breaks often over time) ----
if ! command -v ninja >/dev/null 2>&1; then
  echo "[install] Installing ninja-build..."
  sudo apt-get install -y ninja-build
fi

# ---- Netflix/vmaf (pin to the old Makefile-era tag) ----
if [ ! -d "${DEPS}/vmaf" ]; then
  echo "[install] Fetching vmaf (${VMAF_TAG})..."
  pushd "${DEPS}" >/dev/null
  git clone https://github.com/Netflix/vmaf.git
  popd >/dev/null

  echo "[install] Building vmaf (${VMAF_TAG})..."
  pushd "${DEPS}/vmaf" >/dev/null
  git fetch --tags -f
  git checkout "${VMAF_TAG}" || true
  make -j"$(nproc)"
  popd >/dev/null
else
  echo "[install] vmaf already present, skipping." 
fi

echo
echo "[install] Done."
echo "[install] ffmpeg installed to: ${BIN_DIR}/ffmpeg"
echo "[install] ffprobe installed to: ${BIN_DIR}/ffprobe"
echo "[install] vmaf built in: ${DEPS}/vmaf"
echo
