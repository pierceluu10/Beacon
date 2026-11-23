// Concept 2, "Speech B": a solid B whose stem runs out into a speech-bubble tail, so the letter is the thing that talks.
// Writes concept-2.png (512 tile) and concept-2-64.png. Run: swift concept-2.swift OUTDIR [SIZE]
import AppKit

let out = CommandLine.arguments[1]
let background = NSColor(srgbRed: 0.075, green: 0.078, blue: 0.094, alpha: 1)
let amber = NSColor(srgbRed: 1, green: 0.69, blue: 0.125, alpha: 1)

// Design units: 512 canvas, y down; tile follows the macOS icon grid (824/1024 body, 185.4 corner radius).
let tileRect = CGRect(x: 50, y: 50, width: 412, height: 412), tileCorner: CGFloat = 92
// Letter units: origin at the stem's top-left, cap height 200.
let weight: CGFloat = 38                  // stem, bars and bowl walls
let capHeight: CGFloat = 200
let middleBar: CGFloat = 94               // centerline of the shared middle bar; above half, as in a classic B
let upperWidth: CGFloat = 146, lowerWidth: CGFloat = 160
let corner: CGFloat = 20                  // the bubble's top-left corner
let tailDrop: CGFloat = 40, tailRun: CGFloat = 58   // tail tip below the baseline, and where it rejoins the baseline
let origin = CGPoint(x: 256 - lowerWidth / 2 - 4, y: 256 - capHeight / 2 - 12)  // lifted: the tail is light, the bowls heavy

/// A D-shaped bowl: flat on the left, a semicircle on the right, spanning top...bottom and reaching `right`.
func bowl(_ path: CGMutablePath, left: CGFloat, top: CGFloat, bottom: CGFloat, right: CGFloat) {
    let radius = (bottom - top) / 2
    path.move(to: CGPoint(x: left, y: top))
    path.addLine(to: CGPoint(x: right - radius, y: top))
    path.addArc(center: CGPoint(x: right - radius, y: top + radius), radius: radius, startAngle: -.pi / 2, endAngle: .pi / 2, clockwise: false)
    path.addLine(to: CGPoint(x: left, y: bottom))
    path.closeSubpath()
}

func glyph(_ context: CGContext) {
    context.translateBy(x: origin.x, y: origin.y)
    let upperBottom = middleBar + weight / 2, lowerTop = middleBar - weight / 2

    let ink = CGMutablePath()
    ink.move(to: CGPoint(x: 0, y: corner))
    ink.addArc(tangent1End: .zero, tangent2End: CGPoint(x: weight, y: 0), radius: corner)
    ink.addLine(to: CGPoint(x: corner * 2, y: 0))
    ink.addLine(to: CGPoint(x: tailRun, y: capHeight))
    ink.addLine(to: CGPoint(x: 0, y: capHeight + tailDrop))
    ink.closeSubpath()
    bowl(ink, left: corner, top: 0, bottom: upperBottom, right: upperWidth)
    bowl(ink, left: corner, top: lowerTop, bottom: capHeight, right: lowerWidth)
    context.addPath(ink); context.setFillColor(amber.cgColor); context.fillPath()

    let counters = CGMutablePath()
    bowl(counters, left: weight, top: weight, bottom: upperBottom - weight, right: upperWidth - weight)
    bowl(counters, left: weight, top: lowerTop + weight, bottom: capHeight - weight, right: lowerWidth - weight)
    context.addPath(counters); context.setFillColor(background.cgColor); context.fillPath()
}

func render(size: Int) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let context = NSGraphicsContext(bitmapImageRep: rep)!.cgContext
    context.translateBy(x: 0, y: CGFloat(size)); context.scaleBy(x: 1, y: -1)
    context.scaleBy(x: CGFloat(size) / 512, y: CGFloat(size) / 512)
    context.addPath(CGPath(roundedRect: tileRect, cornerWidth: tileCorner, cornerHeight: tileCorner, transform: nil))
    context.setFillColor(background.cgColor); context.fillPath()
    glyph(context)
    return rep
}

let sizes = CommandLine.arguments.count > 2 ? [(Int(CommandLine.arguments[2])!, "concept-2-\(CommandLine.arguments[2]).png")]
                                            : [(512, "concept-2.png"), (64, "concept-2-64.png")]
for (size, name) in sizes {
    try! render(size: size).representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out).appendingPathComponent(name))
}
