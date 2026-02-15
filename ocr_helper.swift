import Cocoa
import Vision

guard CommandLine.arguments.count > 1 else {
    fputs("Usage: ocr_helper <image_path>\n", stderr)
    exit(1)
}

let path = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: path),
      let tiffData = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiffData),
      let cgImage = bitmap.cgImage else {
    fputs("Cannot load image\n", stderr)
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
do {
    try handler.perform([request])
} catch {
    fputs("Vision error: \(error)\n", stderr)
    exit(1)
}

guard let observations = request.results else { exit(0) }
for observation in observations {
    guard let candidate = observation.topCandidates(1).first else { continue }
    print(candidate.string)
}
