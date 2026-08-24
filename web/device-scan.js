(() => {
  let activeStream = null;
  let cancelled = false;

  async function stopScan() {
    cancelled = true;
    if (activeStream) {
      activeStream.getTracks().forEach((track) => track.stop());
      activeStream = null;
    }
    const video = document.querySelector("#scannerVideo");
    if (video) video.srcObject = null;
  }

  async function scanFullSN() {
    if (!("BarcodeDetector" in window)) {
      throw new Error("当前浏览器不支持本地条码识别，请改用 OCR 结果或手工输入完整 SN");
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("当前环境无法打开摄像头，请改用 OCR 结果或手工输入完整 SN");
    }
    cancelled = false;
    const detector = new BarcodeDetector({ formats: ["code_128", "code_39", "data_matrix", "qr_code"] });
    const video = document.querySelector("#scannerVideo");
    activeStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
    video.srcObject = activeStream;
    await video.play();
    const started = Date.now();
    while (!cancelled && Date.now() - started < 20000) {
      const codes = await detector.detect(video);
      const value = String(codes[0]?.rawValue || "").trim();
      if (value) {
        await stopScan();
        return value;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 180));
    }
    await stopScan();
    throw new Error("20 秒内没有识别到条码，请调整距离，或改用 OCR / 手工输入");
  }

  window.IDCAIDeviceScan = { scanFullSN, stopScan };
})();
