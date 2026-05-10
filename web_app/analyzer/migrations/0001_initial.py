from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Review',
            fields=[
                ('id',         models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text',       models.TextField()),
                ('status',     models.CharField(choices=[('PENDING', 'Pending'), ('PROCESSING', 'Processing'), ('PROCESSED', 'Processed'), ('FAILED', 'Failed')], default='PENDING', max_length=20)),
                ('sentiment',  models.CharField(blank=True, choices=[('POSITIVE', 'Positive'), ('NEGATIVE', 'Negative'), ('NEUTRAL', 'Neutral')], max_length=20, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
