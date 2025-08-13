import argparse
import os
from analyzer import MultiContractAnalyzer


def main():
    parser = argparse.ArgumentParser(description='Multi-contract Solidity reentrancy analyzer')
    parser.add_argument('build_path', help='Path to build-info directory or JSON file')
    parser.add_argument('--output-dir', default='.', help='Output directory')
    parser.add_argument('--report', default='cross_contract_reentrancy_report.txt', help='Report filename')

    args = parser.parse_args()

    analyzer = MultiContractAnalyzer()

    try:
        contexts = analyzer.load_build_info(args.build_path)

        if not contexts:
            print("No contracts found in build info")
            return 1

        print(f"Loaded {len(contexts)} contracts")

        analyzer.analyze_contracts(contexts)

        analyzer.generate_report(os.path.join(args.output_dir, args.report))

        print("\nAnalysis Summary:")
        print(f"- {len(analyzer.all_contracts)} contracts")
        print(f"- {len(analyzer.all_functions)} functions")
        print(f"- {len(analyzer.reentrancy_patterns)} reentrancy patterns detected")

        critical = [p for p in analyzer.reentrancy_patterns 
                   if p.get('severity') == 'critical']
        if critical:
            print(f"\nCRITICAL: {len(critical)} confirmed reentrancy vulnerabilities found!")
            for pattern in critical:
                print(f"  - {pattern['function']}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
