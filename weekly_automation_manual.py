#!/usr/bin/env python3
"""
Weekly Dashboard Automation - Manual Compare URL Version

Use this when you want to provide the GitHub compare URL directly.

Usage:
    python weekly_automation_manual.py \
        --compare-url "https://github.com/usespeakeasy/speak-android/compare/release/4.32.0...release/4.33.0" \
        --claude-token YOUR_CLAUDE_TOKEN \
        --skip-git
"""

import argparse
import re
from datetime import datetime
from pathlib import Path
from anthropic import Anthropic


# Your exact prompt template
CLAUDE_PROMPT_TEMPLATE = """You are acting as a Lead QA Analyst and Release Risk Assessor. Review the RC changelog at this link: {compare_url}. Generate a concise QA risk summary based on all commits with the following structure:

Overall Release Risk Level: [provide level based on your analysis]
• Provide a 1-3 sentence summary explaining the risk level and key drivers

P0 — Top QA Focus Areas (Must Test)
• List only the highest-risk changes with ticket numbers and brief reasoning

Other Notable Changes
• List any concerns (e.g., lack of tests, large diff, unclear intent, multiple PRs touching same area)

No Code Changes (Zero Risk)
• List areas/features with no code changes in a single bullet point, separated by commas

Style Guidelines:
• Use bullet points only
• Be concise and scannable
• Include ticket numbers (e.g., VOICE-827, NUX-1844)
• Focus on P0 items only - do not include P1 or P2 sections
• Assume the audience is QA + Engineering leadership"""


def extract_version_from_url(compare_url: str) -> str:
    """Extract version number from compare URL"""
    # Look for pattern like release/4.33.0 or v4.33.0
    match = re.search(r'(?:release/|v)(\d+\.\d+\.\d+)$', compare_url)
    if match:
        return match.group(1)
    return "Unknown"


def get_claude_analysis(compare_url: str, claude_token: str) -> str:
    """Send prompt to Claude and get the risk assessment report"""
    
    client = Anthropic(api_key=claude_token)
    
    # Insert compare URL into your prompt
    prompt = CLAUDE_PROMPT_TEMPLATE.format(compare_url=compare_url)
    
    print("🤖 Sending prompt to Claude...")
    print(f"   Prompt length: {len(prompt)} characters")
    
    # Use web search tool to let Claude access the URL
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search"
            }
        ],
        messages=[{
            "role": "user",
            "content": prompt
        }]
    )
    
    # Extract text from response (handling tool use)
    response_text = ""
    for block in message.content:
        if block.type == "text":
            response_text += block.text
    
    print(f"   ✅ Received response ({len(response_text)} characters)")
    
    return response_text


def parse_claude_response(response: str) -> dict:
    """Parse Claude's response into structured data"""
    
    data = {
        'riskLevel': 'MEDIUM',
        'summary': [],
        'p0Items': [],
        'otherChanges': [],
        'zeroRisk': ''
    }
    
    # Extract risk level
    risk_match = re.search(r'Overall Release Risk Level:\s*(\w+(?:-\w+)?)', response, re.IGNORECASE)
    if risk_match:
        data['riskLevel'] = risk_match.group(1).upper()
    
    # Extract summary (bullets after risk level, before P0 section)
    summary_section = re.search(
        r'Overall Release Risk Level:.*?\n(.*?)(?=P0 —|$)',
        response,
        re.DOTALL | re.IGNORECASE
    )
    if summary_section:
        bullets = re.findall(r'•\s*(.+)', summary_section.group(1))
        data['summary'] = [b.strip() for b in bullets if b.strip()]
    
    # Extract P0 items
    p0_section = re.search(
        r'P0 —.*?\n(.*?)(?=Other Notable Changes|No Code Changes|$)',
        response,
        re.DOTALL | re.IGNORECASE
    )
    if p0_section:
        p0_bullets = re.findall(r'•\s*(.+)', p0_section.group(1))
        for bullet in p0_bullets:
            # Try to extract ticket number
            ticket_match = re.search(r'([A-Z]+-\d+)', bullet)
            ticket = ticket_match.group(1) if ticket_match else ''
            
            # Try to split title and description
            parts = bullet.split('—', 1)
            if len(parts) == 2:
                title = parts[0].strip()
                description = parts[1].strip()
            else:
                title = bullet.strip()
                description = ''
            
            data['p0Items'].append({
                'ticket': ticket,
                'title': title,
                'description': description,
                'full_text': bullet.strip()
            })
    
    # Extract Other Notable Changes
    other_section = re.search(
        r'Other Notable Changes.*?\n(.*?)(?=No Code Changes|$)',
        response,
        re.DOTALL | re.IGNORECASE
    )
    if other_section:
        other_bullets = re.findall(r'•\s*(.+)', other_section.group(1))
        data['otherChanges'] = [b.strip() for b in other_bullets if b.strip()]
    
    # Extract zero risk areas
    zero_section = re.search(
        r'No Code Changes.*?\n•\s*(.+)',
        response,
        re.DOTALL | re.IGNORECASE
    )
    if zero_section:
        data['zeroRisk'] = zero_section.group(1).strip()
    
    return data


def update_main_dashboard(data: dict, version: str, week_of: str, report_date: str):
    """Update the Weekly RC Release Risk section on index.html"""
    
    print("\n📝 Updating main dashboard (index.html)...")
    
    index_path = Path('index.html')
    if not index_path.exists():
        raise FileNotFoundError("index.html not found. Make sure you're in the correct directory.")
    
    content = index_path.read_text()
    
    # Update risk level badge
    risk_class = f'risk-{data["riskLevel"].lower().replace("-", "")}'
    old_pattern = r'<span class="risk-level risk-\w+">[^<]+</span>'
    new_text = f'<span class="risk-level {risk_class}">{data["riskLevel"]}</span>'
    content = re.sub(old_pattern, new_text, content, count=1)
    
    # Update release version and date
    content = re.sub(
        r'<strong>Android RC [\d.]+</strong> • Week of [^<]+',
        f'<strong>Android RC {version}</strong> • Week of {week_of}',
        content,
        count=1
    )
    
    # Update P0 count
    p0_count = len(data['p0Items'])
    content = re.sub(
        r'<div class="stat-mini-value" style="color: #d44c47;">\d+</div>\s*<div class="stat-mini-label">P0 Items',
        f'<div class="stat-mini-value" style="color: #d44c47;">{p0_count}</div>\n                        <div class="stat-mini-label">P0 Items',
        content,
        count=1
    )
    
    # Update red flags count
    red_flags_count = len(data['otherChanges'])
    content = re.sub(
        r'<div class="stat-mini-value" style="color: #cb912f;">\d+</div>\s*<div class="stat-mini-label">Red Flags',
        f'<div class="stat-mini-value" style="color: #cb912f;">{red_flags_count}</div>\n                        <div class="stat-mini-label">Red Flags',
        content,
        count=1
    )
    
    # Update top 3 P0 concerns
    top_3_html = ''
    for i, item in enumerate(data['p0Items'][:3]):
        top_3_html += f'''
                <div class="risk-item critical">
                    <div class="risk-item-title">{item['title']}</div>
                    <div class="risk-item-description">{item['description'] if item['description'] else item['full_text']}</div>
                </div>
                '''
    
    # Replace the top concerns section
    content = re.sub(
        r'(<div style="margin-bottom: 8px;">.*?</div>\s*)((?:<div class="risk-item critical">.*?</div>\s*){1,3})',
        f'\\1{top_3_html.strip()}\n                ',
        content,
        flags=re.DOTALL,
        count=1
    )
    
    # Update "View Full Report" link
    content = re.sub(
        r'<a href="reports/[\d-]+\.html" class="view-full-report">',
        f'<a href="reports/{report_date}.html" class="view-full-report">',
        content,
        count=1
    )
    
    # Update footer timestamp
    timestamp = datetime.now().strftime("%B %d, %Y at %I:%M %p PST")
    content = re.sub(
        r'<span class="last-updated">Last Updated:</span> [^•]+',
        f'<span class="last-updated">Last Updated:</span> {timestamp}',
        content
    )
    
    index_path.write_text(content)
    print("   ✅ Main dashboard updated")


def create_report_page(claude_response: str, data: dict, version: str, week_of: str, report_date: str):
    """Create a new detailed report page from template"""
    
    print(f"\n📝 Creating new report page (reports/{report_date}.html)...")
    
    template_path = Path('report-template.html')
    if not template_path.exists():
        raise FileNotFoundError("report-template.html not found")
    
    report_path = Path(f'reports/{report_date}.html')
    report_path.parent.mkdir(exist_ok=True, parents=True)
    
    content = template_path.read_text()
    
    # Update title and version
    content = content.replace('Android RC 4.33.0', f'Android RC {version}')
    content = content.replace('Jan 27, 2026', week_of)
    content = content.replace('January 27, 2026', week_of)
    
    # Update risk level badge
    risk_class = f'risk-{data["riskLevel"].lower().replace("-", "")}'
    content = re.sub(
        r'<div class="risk-badge risk-\w+">.*?</div>',
        f'<div class="risk-badge {risk_class}">⚠️ {data["riskLevel"]} RISK</div>',
        content,
        count=1
    )
    
    # Update summary box
    summary_bullets = '<br>\n                '.join([f'• {s}' for s in data['summary']])
    content = re.sub(
        r'(<div class="summary-box">.*?<strong>Overall Risk Level: )[^<]+(</strong><br>\n)(.*?)(</div>)',
        f'\\1{data["riskLevel"]}\\2                {summary_bullets}\n            \\4',
        content,
        flags=re.DOTALL,
        count=1
    )
    
    # Build P0 items HTML
    p0_html = ''
    for item in data['p0Items']:
        p0_html += f'''
            <div class="risk-item critical">
                <div class="risk-item-title">
                    <span class="emoji">🎯</span>
                    {item['title']}
                </div>
                <div class="risk-item-description">
                    {item['description'] if item['description'] else ''}
                </div>
                <div class="risk-item-impact">
                    {item['full_text']}
                </div>
            </div>
'''
    
    # Replace P0 section
    content = re.sub(
        r'(<div class="section-title">.*?P0 —.*?</div>)(.*?)(</div>\s*<!-- ={40})',
        f'\\1\n{p0_html}\n        \\3',
        content,
        flags=re.DOTALL,
        count=1
    )
    
    # Update zero risk section
    if data['zeroRisk']:
        content = re.sub(
            r'(Localizations \(string updates only\).*?Smart Review)',
            data['zeroRisk'],
            content,
            flags=re.DOTALL
        )
    
    # Build red flags HTML (from Other Notable Changes)
    red_flags_html = ''
    for change in data['otherChanges']:
        red_flags_html += f'''
            <div class="risk-item high">
                <div class="risk-item-title">
                    <span class="emoji">⚠️</span>
                    {change}
                </div>
            </div>
'''
    
    # Replace red flags section
    if red_flags_html:
        content = re.sub(
            r'(<div class="section-title">.*?Red Flags.*?</div>)(.*?)(</div>\s*<!-- ={40})',
            f'\\1\n{red_flags_html}\n        \\3',
            content,
            flags=re.DOTALL,
            count=1
        )
    
    # Update footer timestamp
    timestamp = datetime.now().strftime("%B %d, %Y at %I:%M %p PST")
    content = re.sub(
        r'Generated by Claude AI • [^<]+<br>',
        f'Generated by Claude AI • {timestamp}<br>',
        content
    )
    content = re.sub(
        r'Report ID: Android RC [\d.]+',
        f'Report ID: Android RC {version}',
        content
    )
    
    report_path.write_text(content)
    print(f"   ✅ Report created at reports/{report_date}.html")


def save_full_report(claude_response: str, report_date: str):
    """Save Claude's full text response for reference"""
    report_path = Path(f'claude_reports/report_{report_date}.md')
    report_path.parent.mkdir(exist_ok=True, parents=True)
    report_path.write_text(claude_response)
    print(f"   📄 Full Claude response saved to {report_path}")


def main():
    parser = argparse.ArgumentParser(description='Weekly dashboard update with manual compare URL')
    parser.add_argument('--compare-url', required=True, help='GitHub compare URL')
    parser.add_argument('--claude-token', required=True, help='Claude API key')
    parser.add_argument('--skip-git', action='store_true', help='Skip git commit/push')
    parser.add_argument('--date', help='Report date (YYYY-MM-DD), defaults to today')
    parser.add_argument('--version', help='Version (e.g., 4.33.0), auto-extracted from URL if not provided')
    
    args = parser.parse_args()
    
    report_date = args.date or datetime.now().strftime('%Y-%m-%d')
    week_of = datetime.now().strftime('%B %d, %Y')
    version = args.version or extract_version_from_url(args.compare_url)
    
    print("=" * 60)
    print("🚀 WEEKLY DASHBOARD AUTOMATION")
    print("=" * 60)
    
    try:
        # STEP 1: Show compare URL
        print(f"\n📊 STEP 1: Using provided compare URL...")
        print(f"   URL: {args.compare_url}")
        print(f"   Version: {version}")
        
        # STEP 2 & 3: Send to Claude with your prompt
        print("\n🤖 STEP 2-3: Sending prompt to Claude and generating report...")
        claude_response = get_claude_analysis(args.compare_url, args.claude_token)
        
        # Save full response
        save_full_report(claude_response, report_date)
        
        # Parse response
        print("\n📊 Parsing Claude's response...")
        data = parse_claude_response(claude_response)
        print(f"   Risk Level: {data['riskLevel']}")
        print(f"   P0 Items: {len(data['p0Items'])}")
        print(f"   Other Changes: {len(data['otherChanges'])}")
        print(f"   Summary bullets: {len(data['summary'])}")
        
        # STEP 4: Update main dashboard
        print("\n📝 STEP 4: Updating main dashboard...")
        update_main_dashboard(data, version, week_of, report_date)
        
        # STEP 5: Create report page
        print("\n📄 STEP 5: Creating new report page...")
        create_report_page(claude_response, data, version, week_of, report_date)
        
        # Show what to do next
        if args.skip_git:
            print("\n" + "=" * 60)
            print("✅ FILES UPDATED (NOT COMMITTED)")
            print("=" * 60)
            print("\n📋 Review the changes:")
            print("   git diff index.html")
            print(f"   cat reports/{report_date}.html")
            print(f"   cat claude_reports/report_{report_date}.md")
            print("\n📤 If everything looks good, commit and push:")
            print("   git add .")
            print(f"   git commit -m 'Weekly update: {week_of} - Android RC {version}'")
            print("   git push")
        else:
            import subprocess
            print("\n📤 Committing and pushing to GitHub...")
            subprocess.run(['git', 'add', 'index.html', f'reports/{report_date}.html', f'claude_reports/report_{report_date}.md'], check=True)
            subprocess.run(['git', 'commit', '-m', f'Weekly update: {week_of} - Android RC {version}'], check=True)
            subprocess.run(['git', 'push'], check=True)
            print("   ✅ Pushed to GitHub")
        
        print("\n" + "=" * 60)
        print("✅ AUTOMATION COMPLETE!")
        print("=" * 60)
        print(f"\n📊 Summary:")
        print(f"   Version: Android RC {version}")
        print(f"   Risk Level: {data['riskLevel']}")
        print(f"   P0 Items: {len(data['p0Items'])}")
        print(f"   Report Date: {report_date}")
        print(f"\n🌐 View your dashboard at:")
        print(f"   https://tiffanyliang-speak.github.io/risk-dashboard/")
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
