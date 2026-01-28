#!/usr/bin/env python3
"""
Weekly Dashboard Automation - Complete Flow

This script automates the entire weekly dashboard update:
1. Gets GitHub compare link for latest release
2. Adds link to your specific Claude prompt
3. Sends to Claude and gets the report
4. Updates main dashboard "Weekly RC Release Risk" section
5. Creates new report page and updates "View Full Report" link

Usage:
    python weekly_automation.py \
        --repo usespeakeasy/speak-android \
        --github-token YOUR_GITHUB_TOKEN \
        --claude-token YOUR_CLAUDE_TOKEN

Requirements:
    pip install requests anthropic
"""

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
import requests
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


def get_latest_release_branches(repo: str, token: str = None):
    """Get the two most recent release branches from GitHub"""
    url = f"https://api.github.com/repos/{repo}/branches"
    headers = {'Authorization': f'token {token}'} if token else {}
    params = {'per_page': 100}
    
    print("🔍 Fetching release branches from GitHub...")
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    
    branches = response.json()
    
    # Filter release branches and sort by version
    release_pattern = re.compile(r'release/(\d+\.\d+\.\d+)')
    release_branches = []
    
    for branch in branches:
        match = release_pattern.match(branch['name'])
        if match:
            version = tuple(int(x) for x in match.group(1).split('.'))
            release_branches.append({
                'name': branch['name'],
                'version': version,
                'version_string': match.group(1)
            })
    
    # Sort by version (descending)
    release_branches.sort(key=lambda x: x['version'], reverse=True)
    
    if len(release_branches) < 2:
        raise Exception("Need at least 2 release branches to compare")
    
    return release_branches[1], release_branches[0]  # previous, current


def generate_compare_url(repo: str, previous: dict, current: dict) -> str:
    """Generate GitHub compare URL"""
    return f"https://github.com/{repo}/compare/{previous['name']}...{current['name']}"


def get_claude_analysis(compare_url: str, claude_token: str) -> str:
    """Send prompt to Claude and get the risk assessment report"""
    
    client = Anthropic(api_key=claude_token)
    
    # Insert compare URL into your prompt
    prompt = CLAUDE_PROMPT_TEMPLATE.format(compare_url=compare_url)
    
    print("🤖 Sending prompt to Claude...")
    print(f"   Prompt length: {len(prompt)} characters")
    
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[{
            "role": "user",
            "content": prompt
        }]
    )
    
    response_text = message.content[0].text
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
    
    # Find the Weekly RC Release Risk section
    section_pattern = r'(<!-- ={40}\s*SECTION 1: WEEKLY RC RELEASE RISK.*?<!-- UPDATE: Link to this week\'s full report -->.*?</div>)'
    
    # Update risk level badge
    risk_class = f'risk-{data["riskLevel"].lower().replace("-", "")}'
    content = re.sub(
        r'(<span class="risk-level )risk-\w+(">)[^<]+(</span>)',
        f'\\1{risk_class}\\2{data["riskLevel"]}\\3',
        content,
        count=1
    )
    
    # Update release version and date
    content = re.sub(
        r'(<strong>Android RC )[\d.]+(</strong> • Week of )[^<]+',
        f'\\1{version}\\2{week_of}',
        content,
        count=1
    )
    
    # Update P0 count
    p0_count = len(data['p0Items'])
    content = re.sub(
        r'(<div class="stat-mini-value" style="color: #d44c47;">)\d+(</div>\s*<div class="stat-mini-label">P0 Items)',
        f'\\1{p0_count}\\2',
        content,
        count=1
    )
    
    # Update red flags count (using other changes as proxy)
    red_flags_count = len(data['otherChanges'])
    content = re.sub(
        r'(<div class="stat-mini-value" style="color: #cb912f;">)\d+(</div>\s*<div class="stat-mini-label">Red Flags)',
        f'\\1{red_flags_count}\\2',
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
        r'(<a href="reports/)[\d-]+(\.html" class="view-full-report">)',
        f'\\1{report_date}\\2',
        content,
        count=1
    )
    
    # Update footer timestamp
    timestamp = datetime.now().strftime("%B %d, %Y at %I:%M %p PST")
    content = re.sub(
        r'(<span class="last-updated">Last Updated:</span> )[^•]+',
        f'\\1{timestamp}',
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
        r'(Generated by Claude AI • )[^<]+(<br>)',
        f'\\1{timestamp}\\2',
        content
    )
    content = re.sub(
        r'(Report ID: Android RC )[\d.]+(</div>)',
        f'\\1{version} • {week_of}\\2',
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
    parser = argparse.ArgumentParser(description='Automated weekly dashboard update - Complete flow')
    parser.add_argument('--repo', required=True, help='GitHub repo (e.g., usespeakeasy/speak-android)')
    parser.add_argument('--github-token', required=True, help='GitHub personal access token')
    parser.add_argument('--claude-token', required=True, help='Claude API key')
    parser.add_argument('--skip-git', action='store_true', help='Skip git commit/push')
    parser.add_argument('--date', help='Report date (YYYY-MM-DD), defaults to today')
    
    args = parser.parse_args()
    
    report_date = args.date or datetime.now().strftime('%Y-%m-%d')
    week_of = datetime.now().strftime('%B %d, %Y')
    
    print("=" * 60)
    print("🚀 WEEKLY DASHBOARD AUTOMATION")
    print("=" * 60)
    
    try:
        # STEP 1: Get GitHub compare link
        print("\n📊 STEP 1: Getting GitHub compare link...")
        previous, current = get_latest_release_branches(args.repo, args.github_token)
        compare_url = generate_compare_url(args.repo, previous, current)
        
        print(f"   Previous: {previous['name']} ({previous['version_string']})")
        print(f"   Current:  {current['name']} ({current['version_string']})")
        print(f"   Compare:  {compare_url}")
        
        version = current['version_string']
        
        # STEP 2 & 3: Send to Claude with your prompt
        print("\n🤖 STEP 2-3: Sending prompt to Claude and generating report...")
        claude_response = get_claude_analysis(compare_url, args.claude_token)
        
        # Save full response
        save_full_report(claude_response, report_date)
        
        # Parse response
        data = parse_claude_response(claude_response)
        print(f"   Parsed data:")
        print(f"      Risk Level: {data['riskLevel']}")
        print(f"      P0 Items: {len(data['p0Items'])}")
        print(f"      Other Changes: {len(data['otherChanges'])}")
        
        # STEP 4: Update main dashboard
        print("\n📝 STEP 4: Updating main dashboard...")
        update_main_dashboard(data, version, week_of, report_date)
        
        # STEP 5: Create report page
        print("\n📄 STEP 5: Creating new report page...")
        create_report_page(claude_response, data, version, week_of, report_date)
        
        # Git operations
        if not args.skip_git:
            print("\n📤 Committing and pushing to GitHub...")
            subprocess.run(['git', 'add', 'index.html', f'reports/{report_date}.html', f'claude_reports/report_{report_date}.md'], check=True)
            subprocess.run(['git', 'commit', '-m', f'Weekly update: {week_of} - Android RC {version}'], check=True)
            subprocess.run(['git', 'push'], check=True)
            print("   ✅ Pushed to GitHub")
        else:
            print("\n⏭️  Skipping git operations (--skip-git flag)")
        
        print("\n" + "=" * 60)
        print("✅ AUTOMATION COMPLETE!")
        print("=" * 60)
        print(f"\n📊 Summary:")
        print(f"   Version: Android RC {version}")
        print(f"   Risk Level: {data['riskLevel']}")
        print(f"   P0 Items: {len(data['p0Items'])}")
        print(f"   Report Date: {report_date}")
        print(f"\n🌐 View your dashboard at:")
        print(f"   https://yourusername.github.io/risk-dashboard/")
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
