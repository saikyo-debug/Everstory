/*
 * Everstory - voice features (used by voice unlock).
 *
 * Turns a few seconds of speech into 28 numbers that describe how a
 * voice sounds:
 *
 *    13  average MFCC values   (the average shape of the voice's spectrum)
 *    13  MFCC variation        (how much that shape moves while speaking)
 *     2  pitch average + variation (log fundamental frequency)
 *
 * This all happens inside the browser. The recording is never sent
 * anywhere - only these 28 numbers are.
 *
 * Exposed as window.EverstoryVoice:
 *    extract(samples16k)         -> { features: [28], voicedMs }
 *    decodeToMono16k(blob)       -> Float32Array at 16 kHz
 *    recordClip({seconds, onLevel}) -> { features, voiced_ms }
 */
(function (global) {
    "use strict";

    var RATE = 16000;
    var FRAME = 400;          // 25 ms
    var HOP = 160;            // 10 ms
    var NFFT = 512;
    var MEL_FILTERS = 26;
    var MFCC_COUNT = 13;
    var LOW_HZ = 80;
    var HIGH_HZ = 7600;
    var PRE_EMPHASIS = 0.97;

    var PITCH_WINDOW = 640;   // 40 ms
    var PITCH_MIN_HZ = 70;
    var PITCH_MAX_HZ = 400;
    var PITCH_CONFIDENCE = 0.35;

    var DEFAULT_LOG_F0 = Math.log(150);


    /* ---------- Fast Fourier transform (radix 2) ---------- */

    var cosTable = new Float64Array(NFFT / 2);
    var sinTable = new Float64Array(NFFT / 2);

    for (var t = 0; t < NFFT / 2; t += 1) {
        cosTable[t] = Math.cos(2 * Math.PI * t / NFFT);
        sinTable[t] = -Math.sin(2 * Math.PI * t / NFFT);
    }

    var bitReverse = (function () {
        var table = new Uint16Array(NFFT);
        var bits = Math.log2(NFFT);

        for (var i = 0; i < NFFT; i += 1) {
            var reversed = 0;

            for (var b = 0; b < bits; b += 1) {
                if (i & (1 << b)) {
                    reversed |= 1 << (bits - 1 - b);
                }
            }

            table[i] = reversed;
        }

        return table;
    })();

    function fft(real, imag) {
        var n = NFFT;

        for (var i = 0; i < n; i += 1) {
            var j = bitReverse[i];

            if (j > i) {
                var tr = real[i]; real[i] = real[j]; real[j] = tr;
                var ti = imag[i]; imag[i] = imag[j]; imag[j] = ti;
            }
        }

        for (var size = 2; size <= n; size *= 2) {
            var half = size / 2;
            var step = n / size;

            for (var start = 0; start < n; start += size) {
                for (var k = 0; k < half; k += 1) {
                    var wr = cosTable[k * step];
                    var wi = sinTable[k * step];

                    var a = start + k;
                    var b = a + half;

                    var xr = real[b] * wr - imag[b] * wi;
                    var xi = real[b] * wi + imag[b] * wr;

                    real[b] = real[a] - xr;
                    imag[b] = imag[a] - xi;
                    real[a] += xr;
                    imag[a] += xi;
                }
            }
        }
    }


    /* ---------- Mel filter bank and DCT ---------- */

    function hzToMel(hz) { return 2595 * Math.log10(1 + hz / 700); }
    function melToHz(mel) { return 700 * (Math.pow(10, mel / 2595) - 1); }

    var filterBank = (function () {
        var bins = NFFT / 2 + 1;
        var points = [];
        var melLow = hzToMel(LOW_HZ);
        var melHigh = hzToMel(HIGH_HZ);

        for (var i = 0; i < MEL_FILTERS + 2; i += 1) {
            var hz = melToHz(melLow + (melHigh - melLow) * i / (MEL_FILTERS + 1));
            points.push(Math.floor((NFFT + 1) * hz / RATE));
        }

        var bank = [];

        for (var m = 1; m <= MEL_FILTERS; m += 1) {
            var filter = new Float64Array(bins);
            var left = points[m - 1];
            var centre = points[m];
            var right = points[m + 1];

            for (var k = left; k < centre; k += 1) {
                if (centre > left) {
                    filter[k] = (k - left) / (centre - left);
                }
            }

            for (var k2 = centre; k2 <= right; k2 += 1) {
                if (right > centre && k2 < bins) {
                    filter[k2] = (right - k2) / (right - centre);
                }
            }

            bank.push(filter);
        }

        return bank;
    })();

    // Orthonormal DCT-II; keeps coefficients 1..13 (0 is just loudness).
    var dctTable = (function () {
        var table = [];
        var scale = Math.sqrt(2 / MEL_FILTERS);

        for (var k = 1; k <= MFCC_COUNT; k += 1) {
            var row = new Float64Array(MEL_FILTERS);

            for (var m = 0; m < MEL_FILTERS; m += 1) {
                row[m] = scale * Math.cos(
                    Math.PI * k * (m + 0.5) / MEL_FILTERS
                );
            }

            table.push(row);
        }

        return table;
    })();

    var hamming = (function () {
        var window = new Float64Array(FRAME);

        for (var n = 0; n < FRAME; n += 1) {
            window[n] = 0.54 - 0.46 * Math.cos(2 * Math.PI * n / (FRAME - 1));
        }

        return window;
    })();


    /* ---------- Pitch (autocorrelation) ---------- */

    function estimatePitch(samples, start) {
        var end = start + PITCH_WINDOW;

        if (end > samples.length) {
            return null;
        }

        var minLag = Math.floor(RATE / PITCH_MAX_HZ);
        var maxLag = Math.floor(RATE / PITCH_MIN_HZ);

        var mean = 0;

        for (var i = start; i < end; i += 1) {
            mean += samples[i];
        }

        mean /= PITCH_WINDOW;

        var energy = 0;

        for (var i2 = start; i2 < end; i2 += 1) {
            var v = samples[i2] - mean;
            energy += v * v;
        }

        if (energy < 1e-9) {
            return null;
        }

        var bestLag = 0;
        var best = 0;

        for (var lag = minLag; lag <= maxLag; lag += 1) {
            var sum = 0;
            var norm = 0;

            for (var j = start; j + lag < end; j += 1) {
                var a = samples[j] - mean;
                var b = samples[j + lag] - mean;
                sum += a * b;
                norm += b * b;
            }

            var r = sum / Math.sqrt(energy * (norm + 1e-12));

            if (r > best) {
                best = r;
                bestLag = lag;
            }
        }

        if (best < PITCH_CONFIDENCE || bestLag === 0) {
            return null;
        }

        return RATE / bestLag;
    }


    /* ---------- Feature extraction ---------- */

    function round4(value) {
        return Math.round(value * 10000) / 10000;
    }

    function extract(input) {
        var peak = 0;

        for (var p = 0; p < input.length; p += 1) {
            var abs = Math.abs(input[p]);

            if (abs > peak) {
                peak = abs;
            }
        }

        if (peak < 1e-4 || input.length < FRAME * 5) {
            return { features: null, voicedMs: 0 };
        }

        // Make the result independent of how loud the recording was.
        var samples = new Float32Array(input.length);

        for (var s = 0; s < input.length; s += 1) {
            samples[s] = input[s] / peak;
        }

        var frameCount = Math.floor((samples.length - FRAME) / HOP) + 1;

        // Pre-emphasised copy for the spectrum.
        var emphasised = new Float32Array(samples.length);
        emphasised[0] = samples[0];

        for (var e = 1; e < samples.length; e += 1) {
            emphasised[e] = samples[e] - PRE_EMPHASIS * samples[e - 1];
        }

        // Energy per frame, used to find where speech actually is.
        var logEnergy = new Float64Array(frameCount);
        var maxLogEnergy = -Infinity;

        for (var f = 0; f < frameCount; f += 1) {
            var total = 0;
            var offset = f * HOP;

            for (var n = 0; n < FRAME; n += 1) {
                var x = samples[offset + n];
                total += x * x;
            }

            logEnergy[f] = Math.log(total + 1e-10);

            if (logEnergy[f] > maxLogEnergy) {
                maxLogEnergy = logEnergy[f];
            }
        }

        var sorted = Array.prototype.slice.call(logEnergy).sort(function (a, b) {
            return a - b;
        });

        var noiseFloor = sorted[Math.floor(sorted.length * 0.1)];
        var threshold = Math.max(maxLogEnergy - 4.0, noiseFloor + 1.5);

        var voiced = [];

        for (var v = 0; v < frameCount; v += 1) {
            if (logEnergy[v] >= threshold) {
                voiced.push(v);
            }
        }

        if (voiced.length < 20) {
            return { features: null, voicedMs: voiced.length * 10 };
        }

        // MFCCs of every voiced frame.
        var real = new Float64Array(NFFT);
        var imag = new Float64Array(NFFT);
        var mel = new Float64Array(MEL_FILTERS);

        var sum = new Float64Array(MFCC_COUNT);
        var sumSquares = new Float64Array(MFCC_COUNT);

        var pitches = [];

        for (var i = 0; i < voiced.length; i += 1) {
            var start = voiced[i] * HOP;

            real.fill(0);
            imag.fill(0);

            for (var k = 0; k < FRAME; k += 1) {
                real[k] = emphasised[start + k] * hamming[k];
            }

            fft(real, imag);

            for (var m = 0; m < MEL_FILTERS; m += 1) {
                var filter = filterBank[m];
                var energy = 0;

                for (var bin = 0; bin <= NFFT / 2; bin += 1) {
                    if (filter[bin] !== 0) {
                        energy += filter[bin] * (
                            real[bin] * real[bin] + imag[bin] * imag[bin]
                        );
                    }
                }

                mel[m] = Math.log(energy + 1e-10);
            }

            for (var c = 0; c < MFCC_COUNT; c += 1) {
                var row = dctTable[c];
                var coefficient = 0;

                for (var q = 0; q < MEL_FILTERS; q += 1) {
                    coefficient += row[q] * mel[q];
                }

                sum[c] += coefficient;
                sumSquares[c] += coefficient * coefficient;
            }

            // Pitch on every second voiced frame (saves time).
            if (i % 2 === 0) {
                var hz = estimatePitch(samples, start);

                if (hz !== null) {
                    pitches.push(Math.log(hz));
                }
            }
        }

        var count = voiced.length;
        var features = [];

        for (var c1 = 0; c1 < MFCC_COUNT; c1 += 1) {
            features.push(round4(sum[c1] / count));
        }

        for (var c2 = 0; c2 < MFCC_COUNT; c2 += 1) {
            var mean = sum[c2] / count;
            var variance = Math.max(0, sumSquares[c2] / count - mean * mean);
            features.push(round4(Math.sqrt(variance)));
        }

        if (pitches.length >= 5) {
            var pitchMean = 0;

            pitches.forEach(function (value) { pitchMean += value; });
            pitchMean /= pitches.length;

            var pitchVariance = 0;

            pitches.forEach(function (value) {
                pitchVariance += (value - pitchMean) * (value - pitchMean);
            });

            pitchVariance /= pitches.length;

            features.push(round4(pitchMean));
            features.push(round4(Math.sqrt(pitchVariance)));
        } else {
            features.push(round4(DEFAULT_LOG_F0));
            features.push(0);
        }

        return { features: features, voicedMs: voiced.length * 10 };
    }


    /* ---------- Decoding and recording ---------- */

    function decodeToMono16k(blob) {
        var AudioContextClass = global.AudioContext || global.webkitAudioContext;

        if (!AudioContextClass) {
            return Promise.reject(new Error("Audio is not supported."));
        }

        var context = new AudioContextClass();

        return blob.arrayBuffer().then(function (buffer) {
            return new Promise(function (resolve, reject) {
                context.decodeAudioData(buffer, resolve, reject);
            });
        }).then(function (decoded) {
            var length = Math.ceil(decoded.duration * RATE);
            var offline = new OfflineAudioContext(1, length, RATE);
            var source = offline.createBufferSource();

            source.buffer = decoded;
            source.connect(offline.destination);
            source.start(0);

            return offline.startRendering();
        }).then(function (rendered) {
            context.close();
            return rendered.getChannelData(0).slice();
        }, function (error) {
            context.close();
            throw error;
        });
    }

    function recordClip(options) {
        var seconds = options.seconds || 4;
        var onLevel = options.onLevel || function () {};

        if (!global.MediaRecorder || !navigator.mediaDevices ||
            !navigator.mediaDevices.getUserMedia) {
            return Promise.reject(new Error("unsupported"));
        }

        return navigator.mediaDevices.getUserMedia({
            audio: {
                echoCancellation: false,
                noiseSuppression: false,
                autoGainControl: false
            }
        }).then(function (stream) {
            return new Promise(function (resolve, reject) {
                var chunks = [];
                var recorder;

                try {
                    recorder = new MediaRecorder(stream);
                } catch (error) {
                    stream.getTracks().forEach(function (track) { track.stop(); });
                    reject(error);
                    return;
                }

                // Live level meter.
                var AudioContextClass = global.AudioContext || global.webkitAudioContext;
                var meterContext = null;
                var analyser = null;
                var meterData = null;
                var frameId = null;

                try {
                    meterContext = new AudioContextClass();
                    var source = meterContext.createMediaStreamSource(stream);
                    analyser = meterContext.createAnalyser();
                    analyser.fftSize = 512;
                    source.connect(analyser);
                    meterData = new Uint8Array(analyser.fftSize);
                } catch (error) {
                    analyser = null;
                }

                function tick() {
                    if (analyser) {
                        analyser.getByteTimeDomainData(meterData);

                        var maxDeviation = 0;

                        for (var i = 0; i < meterData.length; i += 1) {
                            var deviation = Math.abs(meterData[i] - 128) / 128;

                            if (deviation > maxDeviation) {
                                maxDeviation = deviation;
                            }
                        }

                        onLevel(Math.min(1, maxDeviation * 2.2));
                    }

                    frameId = global.requestAnimationFrame(tick);
                }

                recorder.ondataavailable = function (event) {
                    if (event.data && event.data.size) {
                        chunks.push(event.data);
                    }
                };

                recorder.onstop = function () {
                    global.cancelAnimationFrame(frameId);
                    onLevel(0);

                    stream.getTracks().forEach(function (track) { track.stop(); });

                    if (meterContext) {
                        meterContext.close();
                    }

                    var blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });

                    decodeToMono16k(blob).then(function (samples) {
                        var result = extract(samples);

                        if (!result.features) {
                            resolve({ features: null, voiced_ms: result.voicedMs });
                            return;
                        }

                        resolve({
                            features: result.features,
                            voiced_ms: result.voicedMs
                        });
                    }, reject);
                };

                recorder.start();
                tick();

                global.setTimeout(function () {
                    if (recorder.state !== "inactive") {
                        recorder.stop();
                    }
                }, seconds * 1000);
            });
        });
    }

    global.EverstoryVoice = {
        FEATURE_COUNT: MFCC_COUNT * 2 + 2,
        extract: extract,
        decodeToMono16k: decodeToMono16k,
        recordClip: recordClip
    };

})(window);
