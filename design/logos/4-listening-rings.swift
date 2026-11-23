// Beacon concept 2, "Pulse": a dot sending two rings, broken where a cursor cuts through. Writes concept-2.png and concept-2-64.png.
import AppKit

let out = CommandLine.arguments[1]
let ink = NSColor(srgbRed: 0.96, green: 0.96, blue: 0.97, alpha: 1)
let amber = NSColor(srgbRed: 1, green: 0.69, blue: 0.125, alpha: 1)
let background = NSColor(srgbRed: 0.075, green: 0.078, blue: 0.098, alpha: 1)

let center = CGPoint.zero
let glyphSize: CGFloat = 272  // the ink's longer side, inside the 412 tile
let dotRadius: CGFloat = 34
let ringRadii: [CGFloat] = [82, 136], ringWidth: CGFloat = 28
let cursorTip = CGPoint(x: 30, y: 30), cursorScale: CGFloat = 1.05, cursorClearance: CGFloat = 18
// The classic pointer, tip at the origin, pointing up-left.
let pointer: [CGPoint] = [(0, 0), (0, 150), (40, 114), (66, 170), (96, 157), (70, 102), (122, 102)].map { CGPoint(x: $0.0, y: $0.1) }
let cursorCorner: CGFloat = 14

let cursorPath: CGPath = {
    let transform = CGAffineTransform(translationX: cursorTip.x, y: cursorTip.y).scaledBy(x: cursorScale, y: cursorScale)
    let path = CGMutablePath(); path.addLines(between: pointer, transform: transform); path.closeSubpath()
    return path.copy(strokingWithWidth: cursorCorner, lineCap: .round, lineJoin: .round, miterLimit: 1).union(path)
}()

func glyph(_ context: CGContext) {
    let outer = ringRadii.last! + ringWidth / 2
    let bounds = CGRect(x: -outer, y: -outer, width: outer * 2, height: outer * 2).union(cursorPath.boundingBoxOfPath)
    let scale = glyphSize / max(bounds.width, bounds.height)
    context.translateBy(x: 256, y: 256); context.scaleBy(x: scale, y: scale); context.translateBy(x: -bounds.midX, y: -bounds.midY)
    context.setFillColor(ink.cgColor)
    context.fillEllipse(in: CGRect(x: center.x - dotRadius, y: center.y - dotRadius, width: dotRadius * 2, height: dotRadius * 2))
    // Knock the cursor's padded silhouette out of the rings so it reads as cutting through them.
    context.saveGState()
    let knockout = CGMutablePath()
    knockout.addRect(CGRect(x: -512, y: -512, width: 1024, height: 1024))
    knockout.addPath(cursorPath.copy(strokingWithWidth: cursorClearance * 2, lineCap: .round, lineJoin: .round, miterLimit: 1).union(cursorPath))
    context.addPath(knockout); context.clip(using: .evenOdd)
    context.setStrokeColor(ink.cgColor); context.setLineWidth(ringWidth)
    for radius in ringRadii { context.strokeEllipse(in: CGRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2)) }
    context.restoreGState()
    context.setFillColor(amber.cgColor); context.addPath(cursorPath); context.fillPath()
}

func render(size: Int) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let context = NSGraphicsContext(bitmapImageRep: rep)!.cgContext
    context.translateBy(x: 0, y: CGFloat(size)); context.scaleBy(x: 1, y: -1)
    context.scaleBy(x: CGFloat(size) / 512, y: CGFloat(size) / 512)  // design space is 512
    context.addPath(CGPath(roundedRect: CGRect(x: 50, y: 50, width: 412, height: 412), cornerWidth: 92, cornerHeight: 92, transform: nil))
    context.setFillColor(background.cgColor); context.fillPath()
    glyph(context)
    return rep
}

for (size, name) in [(512, "concept-2.png"), (64, "concept-2-64.png")] {
    try! render(size: size).representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out).appendingPathComponent(name))
}
