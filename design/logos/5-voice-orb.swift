// Beacon concept 3, "Orb": a listening orb with a voice waveform cut through it. Writes concept-3.png and concept-3-64.png.
import AppKit

let out = CommandLine.arguments[1]
let amber = NSColor(srgbRed: 1, green: 0.69, blue: 0.125, alpha: 1)
let background = NSColor(srgbRed: 0.075, green: 0.078, blue: 0.098, alpha: 1)

let center = CGPoint(x: 256, y: 256), orbRadius: CGFloat = 132
let barWidth: CGFloat = 26, barPitch: CGFloat = 46
let barHeights: [CGFloat] = [64, 128, 184, 108, 52]  // uneven, like a spoken syllable

func glyph(_ context: CGContext) {
    let shape = CGMutablePath()
    shape.addEllipse(in: CGRect(x: center.x - orbRadius, y: center.y - orbRadius, width: orbRadius * 2, height: orbRadius * 2))
    for (index, height) in barHeights.enumerated() {
        let x = center.x + (CGFloat(index) - CGFloat(barHeights.count - 1) / 2) * barPitch - barWidth / 2
        shape.addRoundedRect(in: CGRect(x: x, y: center.y - height / 2, width: barWidth, height: height),
                             cornerWidth: barWidth / 2, cornerHeight: barWidth / 2)
    }
    context.setFillColor(amber.cgColor); context.addPath(shape); context.fillPath(using: .evenOdd)
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

for (size, name) in [(512, "concept-3.png"), (64, "concept-3-64.png")] {
    try! render(size: size).representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out).appendingPathComponent(name))
}
