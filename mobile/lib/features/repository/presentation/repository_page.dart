import 'package:contractos_mobile/features/repository/presentation/document_page.dart';
import 'package:flutter/material.dart';

/// Repository index — mobile prioritizes viewing executed documents,
/// evidence, download, and status (spec 2.07.39).
class RepositoryPage extends StatelessWidget {
  const RepositoryPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Contract Repository')),
      body: ListView(
        children: [
          ListTile(
            leading: const Icon(Icons.description_outlined),
            title: const Text('Executed documents'),
            subtitle: const Text('View and download'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => const DocumentPage(agreementId: ''),
              ),
            ),
          ),
          const ListTile(
            leading: Icon(Icons.access_time_outlined),
            title: Text('Timeline'),
            subtitle: Text('Lifecycle history'),
            trailing: Icon(Icons.chevron_right),
          ),
          const ListTile(
            leading: Icon(Icons.gavel_outlined),
            title: Text('Evidence'),
            subtitle: Text('Execution evidence ledger'),
            trailing: Icon(Icons.chevron_right),
          ),
        ],
      ),
    );
  }
}