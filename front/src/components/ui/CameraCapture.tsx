import { useRef, useState, useEffect, useCallback } from 'react';
import { Button, Card } from '../ui';

interface Point {
  x: number;
  y: number;
}

interface CameraCaptureProps {
  onCapture: (file: File) => void;
  onCancel: () => void;
  autoCapture?: boolean;
}

interface DocumentCorners {
  topLeft: Point;
  topRight: Point;
  bottomRight: Point;
  bottomLeft: Point;
}

interface QualityIssue {
  type: 'blur' | 'lighting' | 'size' | 'edges' | 'cutoff' | 'aspect';
  severity: 'warning' | 'error';
  message: string;
}

export function CameraCapture({ onCapture, onCancel, autoCapture = true }: CameraCaptureProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const detectionCanvasRef = useRef<HTMLCanvasElement>(null);

  // Set willReadFrequently on the detection canvas via ref callback
  const detectionCanvasRefCallback = (el: HTMLCanvasElement | null) => {
    if (el) {
      (el as HTMLCanvasElement & { willReadFrequently: boolean }).willReadFrequently = true;
    }
  };

  const [stream, setStream] = useState<MediaStream | null>(null);
  const [facingMode, setFacingMode] = useState<'environment' | 'user'>('environment');
  const [error, setError] = useState<string | null>(null);
  const [captured, setCaptured] = useState<string | null>(null);
  const [documentDetected, setDocumentDetected] = useState(false);
  const [documentCorners, setDocumentCorners] = useState<DocumentCorners | null>(null);
  const [captureCooldown, setCaptureCooldown] = useState(false);
  const [previewImage, setPreviewImage] = useState<string | null>(null);
  const [cameraSession, setCameraSession] = useState(0);
  const [qualityIssues, setQualityIssues] = useState<QualityIssue[]>([]);
  const detectionIntervalRef = useRef<number | null>(null);
  const lastCaptureTimeRef = useRef<number>(0);
  const streamRef = useRef<MediaStream | null>(null);

  const stopStream = useCallback(() => {
    streamRef.current?.getTracks().forEach(track => track.stop());
    streamRef.current = null;
    setStream(null);
  }, []);

  // Quality checks for document capture
  const checkImageQuality = useCallback((
    imageData: ImageData, 
    corners: DocumentCorners, 
    videoWidth: number, 
    videoHeight: number
  ): QualityIssue[] => {
    const issues: QualityIssue[] = [];
    const data = imageData.data;
    const width = imageData.width;
    const height = imageData.height;
    
    // 1. Blur detection using Laplacian variance
    let laplacianSum = 0;
    const laplacianKernel = [0, 1, 0, 1, -4, 1, 0, 1, 0];
    let pixelCount = 0;
    
    for (let y = 1; y < height - 1; y += 2) {
      for (let x = 1; x < width - 1; x += 2) {
        let laplacian = 0;
        for (let ky = -1; ky <= 1; ky++) {
          for (let kx = -1; kx <= 1; kx++) {
            const idx = ((y + ky) * width + (x + kx)) * 4;
            const gray = (data[idx] + data[idx + 1] + data[idx + 2]) / 3;
            laplacian += gray * laplacianKernel[(ky + 1) * 3 + (kx + 1)];
          }
        }
        laplacianSum += laplacian * laplacian;
        pixelCount++;
      }
    }
    const laplacianVar = pixelCount > 0 ? laplacianSum / pixelCount : 0;
    if (laplacianVar < 30) {
      issues.push({
        type: 'blur',
        severity: 'error',
        message: 'Imagen borrosa. Mantenga el dispositivo estable y enfóque el documento.'
      });
    }
    
    // 2. Brightness check
    let totalBrightness = 0;
    let pixelCount2 = 0;
    for (let i = 0; i < data.length; i += 4) {
      totalBrightness += (data[i] + data[i + 1] + data[i + 2]) / 3;
      pixelCount2++;
    }
    const avgBrightness = pixelCount2 > 0 ? totalBrightness / pixelCount2 : 0;
    if (avgBrightness < 40) {
      issues.push({
        type: 'lighting',
        severity: 'error',
        message: 'Imagen muy oscura. Mejore la iluminación o use flash.'
      });
    } else if (avgBrightness > 200) {
      issues.push({
        type: 'lighting',
        severity: 'warning',
        message: 'Imagen muy brillante. Evite reflejos y luz directa.'
      });
    }
    
    // 3. Document size check
    const docWidth = Math.max(
      Math.hypot(corners.topRight.x - corners.topLeft.x, corners.topRight.y - corners.topLeft.y),
      Math.hypot(corners.bottomRight.x - corners.bottomLeft.x, corners.bottomRight.y - corners.bottomLeft.y)
    );
    const docHeight = Math.max(
      Math.hypot(corners.bottomLeft.x - corners.topLeft.x, corners.bottomLeft.y - corners.topLeft.y),
      Math.hypot(corners.bottomRight.x - corners.topRight.x, corners.bottomRight.y - corners.topRight.y)
    );
    const docArea = docWidth * docHeight;
    const frameArea = videoWidth * videoHeight;
    const areaRatio = docArea / frameArea;

    // Judge size by how much of the frame the longest side covers, so a long
    // narrow receipt roll is not flagged as "too small" just because its area
    // is small.
    const spanRatio = Math.max(docWidth / videoWidth, docHeight / videoHeight);

    if (spanRatio < 0.35) {
      issues.push({
        type: 'size',
        severity: 'error',
        message: 'Documento muy pequeño. Acérquese más al documento.'
      });
    } else if (spanRatio > 0.97 || areaRatio > 0.9) {
      issues.push({
        type: 'size',
        severity: 'warning',
        message: 'Documento muy grande. Aléjese un poco.'
      });
    }
    
    // 4. Document cut off at edges (corners too close to frame edges)
    const margin = 20;
    const cornersList = [
      { name: 'esquina superior izquierda', corner: corners.topLeft },
      { name: 'esquina superior derecha', corner: corners.topRight },
      { name: 'esquina inferior derecha', corner: corners.bottomRight },
      { name: 'esquina inferior izquierda', corner: corners.bottomLeft }
    ];
    
    for (const { name, corner } of cornersList) {
      if (corner.x < margin || corner.x > videoWidth - margin ||
          corner.y < margin || corner.y > videoHeight - margin) {
        issues.push({
          type: 'cutoff',
          severity: 'error',
          message: `Documento cortado en ${name}. Aléjese para incluir todo el documento.`
        });
      }
    }
    
    // 5. Aspect ratio check
    // Receipt rolls are legitimately long and narrow (ratio ~0.3), while invoices
    // are usually close to A4/Letter (ratio ~1.4). Only flag genuinely extreme
    // shapes, which usually mean the document is not fully in frame.
    const aspectRatio = docWidth / docHeight;
    if (aspectRatio < 0.2 || aspectRatio > 5.0) {
      issues.push({
        type: 'aspect',
        severity: 'warning',
        message: 'Proporción inusual. Verifique que el documento esté recto y completo.'
      });
    } else if (aspectRatio < 0.45 || aspectRatio > 2.4) {
      issues.push({
        type: 'aspect',
        severity: 'warning',
        message: 'Proporción alargada. Asegúrese de que las cuatro esquinas estén dentro del marco.'
      });
    }
    
    return issues;
  }, []);

  useEffect(() => {
    async function startCamera() {
      try {
        const mediaStream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode, width: { ideal: 1920 }, height: { ideal: 1080 } }
        });
        setStream(mediaStream);
        streamRef.current = mediaStream;
        if (videoRef.current) {
          videoRef.current.srcObject = mediaStream;
        }
      } catch (e) {
        setError('No se pudo acceder a la cámara. Verifica los permisos.');
        console.error(e);
      }
    }

    startCamera();

    return () => {
      stopStream();
      if (detectionIntervalRef.current) {
        clearInterval(detectionIntervalRef.current);
      }
    };
  }, [facingMode, cameraSession, stopStream]);

  // Crop the video frame to the detected document, keeping a small margin so
  // edges are not clipped. Falls back to the full frame when nothing is detected.
  const cropToDocument = useCallback((
    video: HTMLVideoElement,
    canvas: HTMLCanvasElement,
    corners: DocumentCorners | null
  ) => {
    const vw = video.videoWidth;
    const vh = video.videoHeight;
    const context = canvas.getContext('2d');
    if (!context) return;

    if (!corners) {
      canvas.width = vw;
      canvas.height = vh;
      context.drawImage(video, 0, 0);
      return;
    }

    const xs = [corners.topLeft.x, corners.topRight.x, corners.bottomRight.x, corners.bottomLeft.x];
    const ys = [corners.topLeft.y, corners.topRight.y, corners.bottomRight.y, corners.bottomLeft.y];
    const rawX = Math.min(...xs);
    const rawY = Math.min(...ys);
    const rawW = Math.max(...xs) - rawX;
    const rawH = Math.max(...ys) - rawY;

    // 3% margin, clamped to the frame
    const margin = Math.round(Math.max(rawW, rawH) * 0.03);
    const sx = Math.max(0, Math.round(rawX - margin));
    const sy = Math.max(0, Math.round(rawY - margin));
    const sw = Math.min(vw - sx, Math.round(rawW + margin * 2));
    const sh = Math.min(vh - sy, Math.round(rawH + margin * 2));

    if (sw <= 0 || sh <= 0) {
      canvas.width = vw;
      canvas.height = vh;
      context.drawImage(video, 0, 0);
      return;
    }

    canvas.width = sw;
    canvas.height = sh;
    context.drawImage(video, sx, sy, sw, sh, 0, 0, sw, sh);
  }, []);

  const captureAuto = useCallback(() => {
    if (!videoRef.current || !canvasRef.current) return;

    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!canvas.getContext('2d')) return;

    cropToDocument(video, canvas, documentCorners);

    const dataUrl = canvas.toDataURL('image/jpeg', 0.9);
    setPreviewImage(dataUrl); // Show preview instead of immediate capture
    stopStream();
  }, [documentCorners, stopStream, cropToDocument]);

  // Document detection using edge detection
  const detectDocument = useCallback(() => {
    if (!videoRef.current || !detectionCanvasRef.current || !videoRef.current.videoWidth) return;

    const video = videoRef.current;
    const canvas = detectionCanvasRef.current;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const scale = 0.25; // Process at 1/4 resolution for performance
    canvas.width = video.videoWidth * scale;
    canvas.height = video.videoHeight * scale;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const data = imageData.data;
    const width = canvas.width;
    const height = canvas.height;

    // Simple edge detection using Sobel operator
    const edges = new Uint8ClampedArray(width * height);
    const sobelX = [-1, 0, 1, -2, 0, 2, -1, 0, 1];
    const sobelY = [-1, -2, -1, 0, 0, 0, 1, 2, 1];

    for (let y = 1; y < height - 1; y++) {
      for (let x = 1; x < width - 1; x++) {
        let gx = 0, gy = 0;
        for (let ky = -1; ky <= 1; ky++) {
          for (let kx = -1; kx <= 1; kx++) {
            const idx = ((y + ky) * width + (x + kx)) * 4;
            const gray = (data[idx] + data[idx + 1] + data[idx + 2]) / 3;
            gx += gray * sobelX[(ky + 1) * 3 + (kx + 1)];
            gy += gray * sobelY[(ky + 1) * 3 + (kx + 1)];
          }
        }
        edges[y * width + x] = Math.min(255, Math.sqrt(gx * gx + gy * gy));
      }
    }

    // Find contours (simplified - find large rectangular contours)
    const visited = new Uint8Array(width * height);
    let bestRect: DocumentCorners | null = null;
    let bestArea = 0;

    // Find connected components of edges
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const idx = y * width + x;
        if (edges[idx] > 50 && !visited[idx]) {
          // Flood fill to find connected component
          const stack = [{ x, y }];
          const points: Point[] = [];
          let minX = x, maxX = x, minY = y, maxY = y;

          while (stack.length) {
            const { x: cx, y: cy } = stack.pop()!;
            const cidx = cy * width + cx;
            if (cx < 0 || cx >= width || cy < 0 || cy >= height || visited[cidx] || edges[cidx] <= 50) continue;
            visited[cidx] = 1;
            points.push({ x: cx, y: cy });
            minX = Math.min(minX, cx);
            maxX = Math.max(maxX, cx);
            minY = Math.min(minY, cy);
            maxY = Math.max(maxY, cy);
            stack.push({ x: cx + 1, y: cy }, { x: cx - 1, y: cy }, { x: cx, y: cy + 1 }, { x: cx, y: cy - 1 });
          }

          const area = (maxX - minX) * (maxY - minY);
          const rectWidth = maxX - minX;
          const rectHeight = maxY - minY;
          const aspectRatio = rectWidth / rectHeight;

          // Accept document-like rectangles across a wide aspect range so both
          // long narrow receipt rolls and short wide invoices are detected.
          const minSide = 0.10;
          if (area > width * height * 0.02 && area < width * height * 0.92 &&
              rectWidth > width * minSide && rectHeight > height * minSide &&
              aspectRatio > 0.25 && aspectRatio < 4.0 && points.length > 100) {
            if (area > bestArea) {
              bestArea = area;
              bestRect = {
                topLeft: { x: minX / scale, y: minY / scale },
                topRight: { x: maxX / scale, y: minY / scale },
                bottomRight: { x: maxX / scale, y: maxY / scale },
                bottomLeft: { x: minX / scale, y: maxY / scale }
              };
            }
          }
        }
      }
    }

    if (bestRect) {
      setDocumentCorners(bestRect);
      setDocumentDetected(true);

      // Check image quality
      if (videoRef.current && detectionCanvasRef.current) {
        const video = videoRef.current;
        const canvas = detectionCanvasRef.current;
        const ctx = canvas.getContext('2d');
        if (ctx) {
          const scale = 0.25;
          canvas.width = video.videoWidth * scale;
          canvas.height = video.videoHeight * scale;
          ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
          const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
          const issues = checkImageQuality(imageData, bestRect, video.videoWidth, video.videoHeight);
          setQualityIssues(issues);

          // Only allow auto-capture if no critical issues
          const hasCriticalIssues = issues.some(i => i.severity === 'error');
          if (autoCapture && !captureCooldown && !hasCriticalIssues) {
            const now = Date.now();
            if (now - lastCaptureTimeRef.current > 3000) {
              lastCaptureTimeRef.current = now;
              setCaptureCooldown(true);
              setTimeout(() => {
                captureAuto();
                setTimeout(() => setCaptureCooldown(false), 1000);
              }, 500);
            }
          }
        }
      }
    } else {
      setDocumentDetected(false);
      setDocumentCorners(null);
      setQualityIssues([{ type: 'size', severity: 'error', message: 'No se detecta documento. Asegúrese de que el documento esté visible y bien iluminado.' }]);
    }
  }, [autoCapture, captureCooldown, captureAuto, checkImageQuality]);

  const errorIssues = qualityIssues.filter(i => i.severity === 'error');
  const warningIssues = qualityIssues.filter(i => i.severity === 'warning');
  const isReadyToCapture = documentDetected && errorIssues.length === 0;

  let statusTitle: string;
  let statusHint: string;

  if (isReadyToCapture) {
    statusTitle = warningIssues.length > 0 ? 'Listo para capturar' : '¡Documento cuadrado! Capturando…';
    statusHint = warningIssues.length > 0
      ? warningIssues[0].message
      : 'Mantén el documento quieto y dentro del marco.';
  } else if (documentDetected) {
    statusTitle = 'Ajusta el encuadre';
    statusHint = errorIssues[0]?.message ?? 'Encuadra el documento completo.';
  } else {
    statusTitle = 'Buscando documento…';
    statusHint = 'Coloca la factura dentro del marco, sin cortar las esquinas.';
  }

  const statusStyles = isReadyToCapture
    ? 'bg-green-600 text-white'
    : documentDetected
      ? 'bg-amber-500 text-white'
      : 'bg-gray-700/90 text-white';

  const overlayColor = isReadyToCapture
    ? '#22c55e'
    : documentDetected
      ? '#f59e0b'
      : '#ef4444';

  // Draw detection overlay
  useEffect(() => {
    if (!detectionCanvasRef.current || !documentCorners || !videoRef.current) return;

    const canvas = detectionCanvasRef.current;
    const ctx = canvas.getContext('2d');
    const video = videoRef.current;
    if (!ctx || !video) return;

    const scale = canvas.width / video.videoWidth;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    
    // Draw detection rectangle
    ctx.strokeStyle = overlayColor;
    ctx.lineWidth = isReadyToCapture ? 4 : 3;
    ctx.setLineDash([5, 5]);
    ctx.beginPath();
    ctx.moveTo(documentCorners.topLeft.x * scale, documentCorners.topLeft.y * scale);
    ctx.lineTo(documentCorners.topRight.x * scale, documentCorners.topRight.y * scale);
    ctx.lineTo(documentCorners.bottomRight.x * scale, documentCorners.bottomRight.y * scale);
    ctx.lineTo(documentCorners.bottomLeft.x * scale, documentCorners.bottomLeft.y * scale);
    ctx.closePath();
    ctx.stroke();

    // Draw corner markers
    ctx.fillStyle = overlayColor;
    const corners = [
      documentCorners.topLeft,
      documentCorners.topRight,
      documentCorners.bottomRight,
      documentCorners.bottomLeft
    ];
    corners.forEach(corner => {
      ctx.beginPath();
      ctx.arc(corner.x * scale, corner.y * scale, 8, 0, 2 * Math.PI);
      ctx.fill();
    });
    ctx.setLineDash([]);
  }, [documentCorners, documentDetected, overlayColor, isReadyToCapture]);

  // Start detection loop
  useEffect(() => {
    if (!stream) return;
    detectionIntervalRef.current = window.setInterval(() => {
      detectDocument();
    }, 500); // Detect every 500ms

    return () => {
      if (detectionIntervalRef.current) {
        clearInterval(detectionIntervalRef.current);
      }
    };
  }, [stream, detectDocument]);

  const takePhoto = () => {
    if (!videoRef.current || !canvasRef.current) return;

    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!canvas.getContext('2d')) return;

    cropToDocument(video, canvas, documentCorners);

    const dataUrl = canvas.toDataURL('image/jpeg', 0.9);
    setPreviewImage(dataUrl); // Show preview instead of immediate capture
    stopStream();
  };

  const retake = () => {
    setCaptured(null);
    setPreviewImage(null);
    setQualityIssues([]);
    setDocumentDetected(false);
    setDocumentCorners(null);
    lastCaptureTimeRef.current = 0;
    setCameraSession(prev => prev + 1);
  };

  const switchCamera = () => {
    setFacingMode(prev => prev === 'environment' ? 'user' : 'environment');
  };

  if (error) {
    return (
      <Card className="text-center">
        <p className="text-red-600 mb-4">{error}</p>
        <Button variant="secondary" onClick={onCancel}>Cancelar</Button>
      </Card>
    );
  }

  return (
    <Card className="space-y-4">
      {!captured ? (
        <>
          <div className="relative aspect-video bg-gray-900 rounded-lg overflow-hidden">
            <video
              ref={videoRef}
              autoPlay
              playsInline
              className="w-full h-full object-cover"
            />
            <canvas 
              ref={detectionCanvasRefCallback}
              className="absolute top-0 left-0 w-full h-full pointer-events-none"
            />
            <canvas ref={canvasRef} className="hidden" />
            
            {stream && (
              <>
                <div className="absolute top-3 left-1/2 -translate-x-1/2 w-[calc(100%-1.5rem)]">
                  <div
                    className={`flex items-center gap-2 px-4 py-2 rounded-full text-sm font-semibold shadow-lg ${statusStyles} ${
                      isReadyToCapture ? 'animate-pulse' : ''
                    }`}
                  >
                    <span aria-hidden>
                      {isReadyToCapture ? '✅' : documentDetected ? '⚠️' : '🔍'}
                    </span>
                    <span>{statusTitle}</span>
                  </div>
                  <p className="mt-2 text-center text-xs font-medium text-white drop-shadow-[0_1px_2px_rgba(0,0,0,0.9)]">
                    {statusHint}
                  </p>
                </div>

                {qualityIssues.length > 0 && (
                  <ul className="absolute bottom-16 left-3 right-3 flex flex-col gap-1 pointer-events-none">
                    {errorIssues.slice(0, 2).map((issue, idx) => (
                      <li
                        key={`error-${idx}`}
                        className="text-xs font-medium text-white bg-red-600/90 rounded-md px-2 py-1"
                      >
                        {issue.message}
                      </li>
                    ))}
                    {warningIssues.slice(0, 1).map((issue, idx) => (
                      <li
                        key={`warning-${idx}`}
                        className="text-xs font-medium text-white bg-amber-600/90 rounded-md px-2 py-1"
                      >
                        {issue.message}
                      </li>
                    ))}
                  </ul>
                )}

                <div className="absolute bottom-3 left-3 right-3 flex gap-2 justify-end">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={switchCamera}
                    className="text-white bg-white/20 hover:bg-white/30"
                  >
                    🔄 Cambiar
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={onCancel}
                    className="text-white bg-red-500/80 hover:bg-red-600"
                  >
                    Cancelar
                  </Button>
                  <Button
                    onClick={takePhoto}
                    disabled={!isReadyToCapture}
                    className={`px-6 py-2 ${
                      isReadyToCapture
                        ? 'text-white bg-green-600 hover:bg-green-700'
                        : 'text-gray-400 bg-gray-600 cursor-not-allowed'
                    }`}
                  >
                    📸 Capturar
                  </Button>
                </div>
              </>
            )}
          </div>
          <p className="text-sm text-gray-500 text-center">
            {autoCapture 
              ? 'Apunta la cámara hacia la factura/ticket. Se capturará automáticamente al detectar el documento.'
              : 'Apunta la cámara hacia la factura/ticket y presiona Capturar.'}
          </p>
        </>
      ) : previewImage ? (
        <>
          <div className="relative aspect-video bg-gray-900 rounded-lg overflow-hidden">
            <img src={previewImage} alt="Previsualización" className="w-full h-full object-contain" />
            <div className="absolute bottom-4 left-4 right-4 flex justify-between">
              <Button variant="secondary" onClick={() => setPreviewImage(null)} className="text-white bg-gray-600 hover:bg-gray-700">
                🔄 Rehacer
              </Button>
              <Button onClick={() => {
                // Convert data URL to File and send
                fetch(previewImage!)
                  .then(res => res.blob())
                  .then(blob => {
                    const file = new File([blob], `ticket-${Date.now()}.jpg`, { type: 'image/jpeg' });
                    onCapture(file);
                    setCaptured(previewImage!);
                    setPreviewImage(null);
                  });
              }} className="text-white bg-green-600 hover:bg-green-700 px-6 py-2">
                ✅ Aceptar y procesar
              </Button>
            </div>
          </div>
          <p className="text-sm text-gray-500 text-center mt-2">Revisa la imagen. ¿Los datos se ven correctamente?</p>
        </>
      ) : (
        <>
          <div className="relative aspect-video bg-gray-900 rounded-lg overflow-hidden">
            <img src={captured} alt="Captura" className="w-full h-full object-contain" />
            <div className="absolute bottom-4 left-4 right-4 flex justify-between">
              <Button variant="secondary" onClick={retake} className="text-white bg-gray-600 hover:bg-gray-700">
                🔄 Rehacer
              </Button>
              <Button onClick={() => {
                fetch(captured!)
                  .then(res => res.blob())
                  .then(blob => {
                    const file = new File([blob], `ticket-${Date.now()}.jpg`, { type: 'image/jpeg' });
                    onCapture(file);
                  });
              }} className="text-white bg-green-600 hover:bg-green-700 px-6 py-2">
                ✅ Aceptar y procesar
              </Button>
            </div>
          </div>
        </>
      )}
    </Card>
  );
}
